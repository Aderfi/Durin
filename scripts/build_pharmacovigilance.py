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
from dataclasses import dataclass
from pathlib import Path

from src.data.sources import parse_chembl_moa, parse_sider, parse_twosides
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildInputs:
    """Local input files for the ETL (downloaded beforehand)."""

    sider_se: Path
    twosides: Path
    chembl_moa: Path
    unichem: dict[str, int]


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
        {str(k): v for k, v in parse_twosides(inputs.twosides).items()},
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
        help="JSON mapping of ChEMBL id -> PubChem CID.",
    )
    parser.add_argument("--out", type=Path, default=Path("src/data/pharmacovigilance"))
    args = parser.parse_args()
    unichem = json.loads(args.unichem.read_text(encoding="utf-8"))
    build_datasets(
        BuildInputs(args.sider_se, args.twosides, args.chembl_moa, unichem), args.out
    )


if __name__ == "__main__":
    main()
