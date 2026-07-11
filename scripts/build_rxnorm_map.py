"""Build the RxNorm -> PubChem CID map for TWOSIDES.

Two complementary sources:

1. **PubChem PUG-VIEW annotation** (heading "RXCUI", type "Compound"): a
   deterministic, bulk RxCUI -> Compound cross-reference. Walked page by page.
2. **Name resolution fallback** (optional, ``--twosides``): TWOSIDES carries each
   drug's name, so RxCUIs missing from the annotation map are resolved by name
   via PUG-REST. This lifts coverage for compounds PubChem does not expose in the
   annotation but does index by name.

Writes a two-column ``rxcui\\tcid`` TSV — the ``--rxnorm`` input of
``build_pharmacovigilance.py``. Names that resolve to nothing (biologics,
vaccines, mixtures) are collected into ``<out>.unresolved.tsv`` and summarized;
they are simply absent from the map.

Docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-view (annotations),
      https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest (name -> CID).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import polars as pl
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

_ANNOTATIONS = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/annotations/heading/JSON"
_HEADING_PARAMS = {"heading_type": "Compound", "heading": "RXCUI"}
_NAME_CID = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON"
_TIMEOUT = 30  # seconds
_THROTTLE = 0.25  # seconds between requests (<=5 req/s PUG limit)

_RETRY = retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)


@_RETRY
def _fetch_page(page: int) -> dict:
    """Fetch one PUG-VIEW annotation page; return its ``Annotations`` object."""
    resp = requests.get(
        _ANNOTATIONS, params={**_HEADING_PARAMS, "page": page}, timeout=_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()["Annotations"]


@_RETRY
def resolve_name_to_cid(name: str) -> int | None:
    """Resolve a compound name to its first PubChem CID, or None if not found."""
    url = _NAME_CID.format(name=requests.utils.quote(name))
    resp = requests.get(url, timeout=_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    cids = resp.json().get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def _map_from_annotations(annotations: list[dict]) -> dict[str, int]:
    """Extract ``{rxcui: cid}`` (first linked CID) from annotation records."""
    mapping: dict[str, int] = {}
    for ann in annotations:
        cids = ann.get("LinkedRecords", {}).get("CID")
        if cids:
            mapping[str(ann["SourceID"])] = cids[0]
    return mapping


def _annotation_map(local_dir: Path | None) -> dict[str, int]:
    """Build the RxCUI->CID map from PUG-VIEW annotations (fetched or local)."""
    mapping: dict[str, int] = {}
    if local_dir is not None:
        files = sorted(local_dir.glob("*.json"))
        logger.info("Reading %d local page(s) from %s", len(files), local_dir)
        for path in files:
            anns = json.loads(path.read_text(encoding="utf-8"))["Annotations"][
                "Annotation"
            ]
            mapping.update(_map_from_annotations(anns))
    else:
        first = _fetch_page(1)
        total_pages = first["TotalPages"]
        logger.info("Fetching %d annotation page(s) from PubChem", total_pages)
        for page in range(1, total_pages + 1):
            data = first if page == 1 else _fetch_page(page)
            mapping.update(_map_from_annotations(data["Annotation"]))
            time.sleep(_THROTTLE)
    logger.info("Annotation map: %d rxcui->cid", len(mapping))
    return mapping


def _unique_drugs(twosides_path: Path) -> dict[str, str]:
    """Return ``{rxcui: name}`` for every unique drug in a TWOSIDES CSV.

    Streams the file (scan_csv) so a multi-GB TWOSIDES fits in memory.
    """
    lf = pl.scan_csv(twosides_path, infer_schema_length=0)
    d1 = lf.select(rxcui=pl.col("drug_1_rxnorn_id"), name=pl.col("drug_1_concept_name"))
    d2 = lf.select(rxcui=pl.col("drug_2_rxnorm_id"), name=pl.col("drug_2_concept_name"))
    df = pl.concat([d1, d2]).unique(subset="rxcui").collect(streaming=True)
    return dict(zip(df["rxcui"], df["name"], strict=True))


def _fill_by_name(mapping: dict[str, int], twosides_path: Path, out_path: Path) -> None:
    """Resolve TWOSIDES drugs missing from ``mapping`` by name, in place."""
    drugs = _unique_drugs(twosides_path)
    missing = {rx: name for rx, name in drugs.items() if rx not in mapping}
    logger.info(
        "TWOSIDES has %d unique drugs; resolving %d uncovered by name",
        len(drugs),
        len(missing),
    )

    resolved = 0
    unresolved: list[str] = []
    for rxcui, name in missing.items():
        try:
            cid = resolve_name_to_cid(name)
        except requests.RequestException:
            logger.error("PubChem request failed for %r (rxcui %s)", name, rxcui)
            cid = None
        if cid is None:
            logger.debug("No PubChem CID for %r (rxcui %s)", name, rxcui)
            unresolved.append(f"{rxcui}\t{name}")
        else:
            mapping[rxcui] = cid
            resolved += 1
        time.sleep(_THROTTLE)

    if unresolved:
        skipped_path = out_path.with_suffix(".unresolved.tsv")
        skipped_path.write_text("\n".join(unresolved) + "\n", encoding="utf-8")
        logger.warning(
            "%d TWOSIDES drugs still unresolved (no compound CID); wrote %s",
            len(unresolved),
            skipped_path,
        )
    logger.info("Name fallback resolved %d additional drugs", resolved)


def build_rxnorm_map(
    out_path: Path, local_dir: Path | None = None, twosides: Path | None = None
) -> None:
    """Build the rxcui->cid TSV from annotations, optionally filling gaps by name."""
    mapping = _annotation_map(local_dir)
    if twosides is not None:
        _fill_by_name(mapping, twosides, out_path)
    out_path.write_text(
        "\n".join(f"{rxcui}\t{cid}" for rxcui, cid in mapping.items()) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d rxcui->cid mappings)", out_path, len(mapping))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build RxNorm->PubChem CID TSV (annotation + name fallback)."
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=None,
        help="Parse pre-downloaded annotation *.json pages instead of fetching.",
    )
    parser.add_argument(
        "--twosides",
        type=Path,
        default=None,
        help="TWOSIDES CSV; resolves uncovered drugs by name to lift coverage.",
    )
    parser.add_argument("--out", type=Path, default=Path("tmp/rxnorm_to_cid.tsv"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_rxnorm_map(args.out, local_dir=args.local_dir, twosides=args.twosides)


if __name__ == "__main__":
    main()


# Example (annotation only):
#   python scripts/build_rxnorm_map.py --out tmp/rxnorm_to_cid.tsv
#
# Example (annotation + name fallback for TWOSIDES coverage):
#   python scripts/build_rxnorm_map.py \
#     --twosides tmp/TWOSIDES.csv --out tmp/rxnorm_to_cid.tsv
