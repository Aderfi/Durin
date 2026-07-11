"""Source adapters for pharmacovigilance data (SIDER, ChEMBL, TWOSIDES, openFDA).

Pure parsing and I/O; no Pydantic model assembly (that is ``enrichment.py``).
Every mapping failure is logged — never swallowed.
"""

import re
from pathlib import Path

import polars as pl

from src.utils.logging import get_logger

logger = get_logger(__name__)

# STITCH compound id, e.g. "CID100002244" (flat) or "CID000002244" (stereo).
_STITCH_PATTERN = re.compile(r"^CID[01](\d+)$")

# SIDER meddra_all_se.tsv column order (no header in the distributed file).
_SIDER_SE_COLUMNS = [
    "stitch_flat",
    "stitch_stereo",
    "umls_label",
    "meddra_type",
    "meddra_code",
    "side_effect_name",
]


def stitch_to_cid(stitch_id: str) -> int | None:
    """Convert a STITCH id to a PubChem CID, or None if malformed (logged)."""
    match = _STITCH_PATTERN.match(stitch_id.strip())
    if match is None:
        logger.warning("Unmappable STITCH id, skipping: %r", stitch_id)
        return None
    return int(match.group(1))  # int() drops leading zeros


def parse_sider(se_path: Path, freq_path: Path | None = None) -> dict[int, list[dict]]:
    """Parse SIDER ``meddra_all_se.tsv`` into per-CID raw effect dicts.

    Only PT (Preferred Term) rows are kept. Unmappable STITCH ids are skipped
    and logged by ``stitch_to_cid``. ``freq_path`` is accepted for future
    frequency joining; frequency is left None for now.
    """
    frame = pl.read_csv(
        se_path, separator="\t", has_header=False, new_columns=_SIDER_SE_COLUMNS
    )
    frame = frame.filter(pl.col("meddra_type") == "PT")

    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        cid = stitch_to_cid(row["stitch_flat"])
        if cid is None:
            continue
        by_cid.setdefault(cid, []).append(
            {
                "name": row["side_effect_name"],
                "meddra_pt": row["side_effect_name"],
                "meddra_code": str(row["meddra_code"]),
                "frequency": None,
                "source": "SIDER",
                "source_id": row["stitch_flat"],
            }
        )
    logger.info(
        "Parsed SIDER: %d compounds, %d effect rows",
        len(by_cid),
        sum(len(v) for v in by_cid.values()),
    )
    return by_cid


def parse_twosides(path: Path) -> dict[int, list[dict]]:
    """Parse a TWOSIDES CSV into per-CID interaction dicts (indexed both ways).

    Each drug-drug row yields an entry under both CIDs of the pair so a lookup by
    either compound finds the interaction.
    """
    frame = pl.read_csv(path)
    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        a, b = int(row["drug_1_cid"]), int(row["drug_2_cid"])
        meddra_pt = row["condition_meddra_name"]
        mechanism = f"Increased risk of {meddra_pt} (TWOSIDES PRR={row['prr']})"
        for cid, other in ((a, b), (b, a)):
            by_cid.setdefault(cid, []).append(
                {
                    "interacting_cid": other,
                    "mechanism": mechanism,
                    "meddra_pt": meddra_pt,
                    "source": "TWOSIDES",
                    "source_id": f"{a}-{b}",
                }
            )
    logger.info("Parsed TWOSIDES: %d compounds", len(by_cid))
    return by_cid


def parse_chembl_moa(path: Path, unichem: dict[str, int]) -> dict[int, list[dict]]:
    """Parse a ChEMBL mechanism-of-action CSV into per-CID mechanism dicts.

    ``unichem`` maps ChEMBL molecule ids to PubChem CIDs. A ChEMBL id with no
    mapping is logged and skipped (no silent drop).
    """
    frame = pl.read_csv(path)
    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        chembl_id = row["molecule_chembl_id"]
        cid = unichem.get(chembl_id)
        if cid is None:
            logger.warning("No UniChem CID for ChEMBL id, skipping: %s", chembl_id)
            continue
        by_cid.setdefault(cid, []).append(
            {
                "mechanism": row["mechanism_of_action"],
                "action_type": row["action_type"],
                "source": "ChEMBL",
                "source_id": chembl_id,
            }
        )
    logger.info("Parsed ChEMBL MoA: %d compounds", len(by_cid))
    return by_cid
