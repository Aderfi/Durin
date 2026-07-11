"""Offline ETL: build Tier 1 pharmacovigilance datasets keyed by PubChem CID.

Downloads (documented below) are performed separately; this script parses the
local files and writes the per-CID JSON datasets consumed at runtime by
``src.data.enrichment``. The LLM term-normalizer (openFDA text -> MedDRA) is NOT
invoked here yet — it is a placeholder (see pharmacovigilance/normalizer.py).

Source downloads:
  SIDER 4.1    : http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz
  TWOSIDES     : https://tatonetti.c2b2.columbia.edu/nsides/  (CSV export)
  ChEMBL MoA   : ChEMBL DB `mechanism` table export (CSV)
  UniChem map  : https://www.ebi.ac.uk/unichem/  (ChEMBL id -> PubChem CID)
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path.cwd().parent  # el notebook vive en notebooks/ -> raíz del repo
sys.path.insert(
    0, str(ROOT)
)  # para poder importar src.data.sources y src.utils.logging

from src.data.sources import parse_chembl_moa, parse_sider, parse_twosides  # noqa: E402
from src.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


@dataclass
class BuildInputs:
    """Local input files for the ETL (downloaded beforehand)."""

    sider_se: Path
    twosides: Path
    chembl_moa: Path
    unichem: dict[str, int]  # ChEMBL id -> PubChem CID
    rxnorm_to_cid: dict[str, int]  # RxNorm id -> PubChem CID (for TWOSIDES)


def _load_tsv_map(path: Path) -> dict[str, int]:
    """Load a two-column ``id\\tcid`` TSV into ``{id: cid}``.

    Skips any header/malformed line whose second column is not an integer, so
    UniChem's header row is tolerated.
    """
    mapping: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[1].strip().isdigit():
            continue
        mapping[parts[0].strip()] = int(parts[1].strip())
    logger.info("Loaded %d id->CID mappings from %s", len(mapping), path)
    return mapping


def _write(out_dir: Path, name: str, data: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(data), encoding="utf-8")
    logger.info("Wrote %s (%d compounds)", path, len(data))


def build_datasets(inputs: BuildInputs, out_dir: Path) -> None:
    """Parse all sources and write the three per-CID JSON datasets."""
    _write(
        out_dir,
        "sider_effects.json",
        {str(k): v for k, v in parse_sider(inputs.sider_se).items()},
    )
    _write(
        out_dir,
        "twosides_ddi.json",
        {
            str(k): v
            for k, v in parse_twosides(inputs.twosides, inputs.rxnorm_to_cid).items()
        },
    )
    _write(
        out_dir,
        "chembl_moa.json",
        {
            str(k): v
            for k, v in parse_chembl_moa(inputs.chembl_moa, inputs.unichem).items()
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Tier 1 pharmacovigilance datasets."
    )
    parser.add_argument("--sider-se", type=Path, required=True)
    parser.add_argument("--twosides", type=Path, required=True)
    parser.add_argument("--chembl-moa", type=Path, required=True)
    parser.add_argument(
        "--unichem",
        type=Path,
        required=True,
        help="TSV mapping ChEMBL id -> PubChem CID (UniChem src1src22).",
    )
    parser.add_argument(
        "--rxnorm",
        type=Path,
        required=True,
        help="TSV mapping RxNorm id -> PubChem CID (for TWOSIDES).",
    )
    parser.add_argument("--out", type=Path, default=Path("src/data/pharmacovigilance"))
    args = parser.parse_args()
    unichem = _load_tsv_map(args.unichem)
    rxnorm_to_cid = _load_tsv_map(args.rxnorm)

    build_datasets(
        BuildInputs(
            args.sider_se, args.twosides, args.chembl_moa, unichem, rxnorm_to_cid
        ),
        args.out,
    )


if __name__ == "__main__":
    main()

# Example:
#   python scripts/build_pharmacovigilance.py \
#     --sider-se tmp/meddra_all_se.tsv \
#     --twosides tmp/TWOSIDES.csv \
#     --chembl-moa tmp/chembl_moa.csv \
#     --unichem tmp/src1src22.txt \
#     --rxnorm tmp/rxnorm_to_cid.tsv
