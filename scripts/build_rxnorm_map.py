"""Build the RxNorm -> PubChem CID map from PubChem PUG-VIEW annotations.

PubChem publishes an RxCUI -> Compound cross-reference as an annotation
(heading "RXCUI", type "Compound"). Each annotation carries the RxCUI
(``SourceID``) and its linked PubChem compound (``LinkedRecords.CID``). This
script walks every page of that annotation and writes a two-column
``rxcui\\tcid`` TSV — the ``--rxnorm`` input of ``build_pharmacovigilance.py``.

Deterministic and complete: it covers every RxNorm concept that has a linked
compound (~7000), not just the drugs in a given TWOSIDES file. Concepts without
a linked compound (biologics, vaccines, mixtures) are simply absent — no
per-name resolution and no 404 flood.

The annotation lives in PUG-VIEW (paginated). Only ~8 requests are needed.
Docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-view
"""

import argparse
import json
import sys
import time
from pathlib import Path

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
_TIMEOUT = 30  # seconds
_THROTTLE = 0.25  # seconds between page requests (<=5 req/s PUG limit)


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _fetch_page(page: int) -> dict:
    """Fetch one PUG-VIEW annotation page; return its ``Annotations`` object."""
    resp = requests.get(
        _ANNOTATIONS, params={**_HEADING_PARAMS, "page": page}, timeout=_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()["Annotations"]


def _map_from_annotations(annotations: list[dict]) -> dict[str, int]:
    """Extract ``{rxcui: cid}`` from a list of annotation records.

    Uses the first linked CID. Records with no linked compound are skipped.
    """
    mapping: dict[str, int] = {}
    for ann in annotations:
        cids = ann.get("LinkedRecords", {}).get("CID")
        if cids:
            mapping[str(ann["SourceID"])] = cids[0]
    return mapping


def build_rxnorm_map(out_path: Path, local_dir: Path | None = None) -> None:
    """Build the rxcui->cid TSV, fetching PUG-VIEW pages or reading local files.

    ``local_dir``: parse pre-downloaded ``*.json`` annotation pages instead of
    fetching (offline). Otherwise all pages are fetched from PubChem.
    """
    mapping: dict[str, int] = {}
    total_records = 0

    if local_dir is not None:
        files = sorted(local_dir.glob("*.json"))
        logger.info("Reading %d local page(s) from %s", len(files), local_dir)
        for path in files:
            anns = json.loads(path.read_text(encoding="utf-8"))["Annotations"][
                "Annotation"
            ]
            total_records += len(anns)
            mapping.update(_map_from_annotations(anns))
    else:
        first = _fetch_page(1)
        total_pages = first["TotalPages"]
        logger.info("Fetching %d annotation page(s) from PubChem", total_pages)
        for page in range(1, total_pages + 1):
            data = first if page == 1 else _fetch_page(page)
            anns = data["Annotation"]
            total_records += len(anns)
            mapping.update(_map_from_annotations(anns))
            time.sleep(_THROTTLE)

    out_path.write_text(
        "\n".join(f"{rxcui}\t{cid}" for rxcui, cid in mapping.items()) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Wrote %s (%d rxcui->cid mappings from %d records)",
        out_path,
        len(mapping),
        total_records,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build RxNorm->PubChem CID TSV from PubChem PUG-VIEW annotations."
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=None,
        help="Parse pre-downloaded annotation *.json pages instead of fetching.",
    )
    parser.add_argument("--out", type=Path, default=Path("tmp/rxnorm_to_cid.tsv"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_rxnorm_map(args.out, local_dir=args.local_dir)


if __name__ == "__main__":
    main()


# Example (fetch all pages from PubChem):
#   python scripts/build_rxnorm_map.py --out tmp/rxnorm_to_cid.tsv
#
# Example (parse pages you already downloaded into a folder):
#   python scripts/build_rxnorm_map.py \
#     --local-dir tmp/rxnorm_pages --out tmp/rxnorm_to_cid.tsv
