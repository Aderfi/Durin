"""Build the RxNorm -> PubChem CID map that TWOSIDES needs.

TWOSIDES identifies drugs by RxNorm id but also carries each drug's name
(``*_concept_name``). This script extracts every unique (RxNorm id, name) pair
from a TWOSIDES CSV, resolves the name to a PubChem CID via PUG-REST, and writes
a two-column ``rxcui\\tcid`` TSV — the ``--rxnorm`` input of
``build_pharmacovigilance.py``.

Only the drugs present in TWOSIDES are resolved (a bounded set), not all of
RxNorm. Names that do not resolve are logged and skipped (no silent failure).

PUG-REST rate limit: <=5 req/s. A small delay is inserted between calls.
"""

import argparse
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

_NAME_CID = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON"
_TIMEOUT = 15  # seconds
_THROTTLE = 0.25  # seconds between requests (<=5 req/s PUG-REST limit)


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def resolve_name_to_cid(name: str) -> int | None:
    """Resolve a compound name to its first PubChem CID, or None if not found."""
    url = _NAME_CID.format(name=requests.utils.quote(name))
    resp = requests.get(url, timeout=_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    cids = resp.json().get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def _unique_drugs(twosides_path: Path) -> dict[str, str]:
    """Return ``{rxcui: name}`` for every unique drug in a TWOSIDES CSV."""
    frame = pl.read_csv(twosides_path, infer_schema_length=0)
    drugs: dict[str, str] = {}
    for row in frame.iter_rows(named=True):
        drugs.setdefault(row["drug_1_rxnorn_id"], row["drug_1_concept_name"])
        drugs.setdefault(row["drug_2_rxnorm_id"], row["drug_2_concept_name"])
    return drugs


def build_rxnorm_map(twosides_path: Path, out_path: Path) -> None:
    """Resolve every TWOSIDES drug name to a CID and write an rxcui->cid TSV.

    Many TWOSIDES entries are biologics, vaccines or mixtures with no PubChem
    compound (a 404 is expected for those). To avoid flooding the log, each
    unresolved name is logged at DEBUG and collected; a single summary is logged
    at the end and the unresolved list is written to ``<out>.unresolved.tsv`` for
    review. Genuine request failures (network/5xx) are still logged as errors.
    """
    drugs = _unique_drugs(twosides_path)
    logger.info("Resolving %d unique RxNorm drugs to CIDs", len(drugs))

    lines: list[str] = []
    unresolved: list[str] = []
    for rxcui, name in drugs.items():
        try:
            cid = resolve_name_to_cid(name)
        except requests.RequestException:
            logger.error("PubChem request failed for %r (rxcui %s)", name, rxcui)
            cid = None
        if cid is None:
            logger.debug("No PubChem CID for %r (rxcui %s)", name, rxcui)
            unresolved.append(f"{rxcui}\t{name}")
        else:
            lines.append(f"{rxcui}\t{cid}")
        time.sleep(_THROTTLE)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if unresolved:
        skipped_path = out_path.with_suffix(".unresolved.tsv")
        skipped_path.write_text("\n".join(unresolved) + "\n", encoding="utf-8")
        logger.warning(
            "%d/%d drugs unresolved (biologics/vaccines/mixtures typically have "
            "no compound CID); wrote list to %s",
            len(unresolved),
            len(drugs),
            skipped_path,
        )
    logger.info("Wrote %s (%d/%d resolved)", out_path, len(lines), len(drugs))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build RxNorm->PubChem CID TSV from a TWOSIDES CSV."
    )
    parser.add_argument("--twosides", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("tmp/rxnorm_to_cid.tsv"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_rxnorm_map(args.twosides, args.out)


if __name__ == "__main__":
    main()


# Example:
#   python scripts/build_rxnorm_map.py \
#     --twosides tmp/TWOSIDES.csv \
#     --out tmp/rxnorm_to_cid.tsv
