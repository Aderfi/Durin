"""Source adapters for pharmacovigilance data (SIDER, ChEMBL, TWOSIDES, openFDA).

Pure parsing and I/O; no Pydantic model assembly (that is ``enrichment.py``).
Every mapping failure is logged — never swallowed.
"""

import json
import re
from pathlib import Path

import polars as pl
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logging import get_logger

logger = get_logger(__name__)

_OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
_OPENFDA_TIMEOUT = 15  # seconds

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


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _openfda_get(active_name: str) -> dict | None:
    """Query openFDA drug/label by active ingredient; None on 404."""
    params = {"search": f'active_ingredient:"{active_name}"', "limit": 1}
    resp = requests.get(_OPENFDA_LABEL, params=params, timeout=_OPENFDA_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _first(value: list[str] | None) -> str | None:
    """openFDA returns single-element lists for label sections."""
    return value[0] if value else None


def fetch_openfda_label(cid: int, active_name: str, cache_dir: Path) -> dict | None:
    """Fetch openFDA label sections for a CID, caching the result per CID.

    Returns ``{adverse_reactions, mechanism_of_action, source_id}`` or None.
    Reads the cache first; on a miss, calls openFDA and writes the cache.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        payload = _openfda_get(active_name)
    except requests.RequestException:
        logger.error("openFDA request failed for CID %d (%s)", cid, active_name)
        return None
    if not payload or not payload.get("results"):
        logger.warning("No openFDA label for CID %d (%s)", cid, active_name)
        return None

    result = payload["results"][0]
    record = {
        "adverse_reactions": _first(result.get("adverse_reactions")),
        "mechanism_of_action": _first(result.get("mechanism_of_action")),
        "source_id": result.get("set_id", active_name),
    }
    cache_file.write_text(json.dumps(record), encoding="utf-8")
    return record
