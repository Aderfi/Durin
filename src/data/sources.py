"""Source adapters for pharmacovigilance data (SIDER, ChEMBL, TWOSIDES, openFDA).

Pure parsing and I/O; no Pydantic model assembly (that is ``enrichment.py``).
Every mapping failure is logged — never swallowed.
"""

import json
import re
from collections.abc import Iterator
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

# Clause delimiters used to segment an openFDA adverse_reactions blob.
_REACTION_SPLIT = re.compile(r"[\n;,.]+")

# MedDRA codes flagged as Important Medical Events / serious SMQ membership.
# Seeded minimally; extended by the ETL from the MedDRA IME list.
_SERIOUS_MEDDRA_CODES: frozenset[str] = frozenset({"10017955"})

# SIDER meddra_all_se.tsv column order (no header in the distributed file).
# NOTE: SIDER codes side effects by UMLS CUI, not by numeric MedDRA code -- the
# fifth column is the UMLS CUI *for the MedDRA term* (e.g. "C0000729"), so it is
# named ``umls_cui`` here, not ``meddra_code``.
_SIDER_SE_COLUMNS = [
    "stitch_flat",
    "stitch_stereo",
    "umls_label",
    "meddra_type",
    "umls_cui",
    "side_effect_name",
]


def stitch_to_cid(stitch_id: str) -> int | None:
    """Convert a STITCH id to a PubChem CID, or None if malformed (logged)."""
    match = _STITCH_PATTERN.match(stitch_id.strip())
    if match is None:
        logger.warning("Unmappable STITCH id, skipping: %r", stitch_id)
        return None
    return int(match.group(1))  # int() drops leading zeros


def derive_severity(
    meddra_code: str | None, is_serious: bool
) -> tuple[str | None, bool]:
    """Deterministically derive (severity, severity_derived) from MedDRA signal.

    ``severe`` if flagged serious or the code is in the serious set; ``moderate``
    if a code exists but is not serious; ``(None, False)`` if there is no signal.
    This is a rule, not an LLM inference.
    """
    if is_serious or (meddra_code in _SERIOUS_MEDDRA_CODES):
        return "severe", True
    if meddra_code is not None:
        return "moderate", True
    return None, False


def split_adverse_reactions(text: str | None, max_len: int = 80) -> list[str]:
    """Segment an openFDA ``adverse_reactions`` blob into candidate phrases.

    Best-effort deterministic segmentation on newlines and clause delimiters for
    the term normalizer to code -- this is NOT clinical NER. Blank, overlong, or
    case-insensitively duplicate fragments are dropped; the precision-first
    normalizer discards any remaining fragment that is not a real MedDRA term.
    """
    if not text:
        return []
    seen: set[str] = set()
    phrases: list[str] = []
    for chunk in _REACTION_SPLIT.split(text):
        phrase = chunk.strip()
        key = phrase.lower()
        if not phrase or len(phrase) > max_len or key in seen:
            continue
        seen.add(key)
        phrases.append(phrase)
    return phrases


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
                # SIDER has no numeric MedDRA code; it carries a UMLS CUI. A real
                # MedDRA code is only assigned on the openFDA/LLM normalizer path.
                "umls_cui": str(row["umls_cui"]),
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


_TWOSIDES_BATCH = 200_000


def _twosides_map_frame(rxnorm_to_cid: dict[str, int]) -> pl.DataFrame:
    """RxNorm-id -> CID lookup as a Polars frame for vectorized joins."""
    return pl.DataFrame(
        {"rx": list(rxnorm_to_cid.keys()), "cid": list(rxnorm_to_cid.values())},
        schema={"rx": pl.Utf8, "cid": pl.Int64},
    )


def _direction(
    joined: pl.DataFrame, subject: str, other: str, name: str
) -> pl.DataFrame:
    """One direction of the pair: subject CID + interacting CID/name + condition."""
    return joined.select(
        cid=pl.col(subject),
        interacting_cid=pl.col(other),
        interacting_name=pl.col(name),
        mechanism=pl.col("mechanism"),
        meddra_pt=pl.col("condition_concept_name"),
        meddra_code=pl.col("condition_meddra_id"),
        source=pl.lit("TWOSIDES"),
        source_id=pl.col("source_id"),
    )


def _transform_twosides_batch(df: pl.DataFrame, map_df: pl.DataFrame) -> list[dict]:
    """Vectorized transform of one CSV batch into interaction row dicts.

    Inner-joins both drug RxNorm ids to CIDs (pairs with either drug unmapped are
    dropped), then emits a row under both CIDs of the pair.
    """
    joined = df.join(
        map_df.rename({"rx": "drug_1_rxnorn_id", "cid": "cid1"}),
        on="drug_1_rxnorn_id",
        how="inner",
    ).join(
        map_df.rename({"rx": "drug_2_rxnorm_id", "cid": "cid2"}),
        on="drug_2_rxnorm_id",
        how="inner",
    )
    if joined.is_empty():
        return []
    joined = joined.with_columns(
        (
            pl.lit("Increased risk of ")
            + pl.col("condition_concept_name")
            + pl.lit(" (TWOSIDES PRR=")
            + pl.col("PRR")
            + pl.lit(")")
        ).alias("mechanism"),
        (pl.col("drug_1_rxnorn_id") + pl.lit("-") + pl.col("drug_2_rxnorm_id")).alias(
            "source_id"
        ),
    )
    forward = _direction(joined, "cid1", "cid2", "drug_2_concept_name")
    backward = _direction(joined, "cid2", "cid1", "drug_1_concept_name")
    return pl.concat([forward, backward]).to_dicts()


def iter_twosides_rows(
    path: Path, rxnorm_to_cid: dict[str, int], batch_size: int = _TWOSIDES_BATCH
) -> Iterator[dict]:
    """Stream TWOSIDES interaction rows (indexed both ways), bounded in memory.

    TWOSIDES has tens of millions of rows, so it is read in Polars batches and
    transformed vectorized per batch -- never the whole file (nor the whole
    output) in RAM at once. Each yielded dict matches the ``twosides_ddi`` table.
    Pairs whose RxNorm ids are not in ``rxnorm_to_cid`` are dropped by the join.

    Note the source header misspells the first column as ``drug_1_rxnorn_id``.
    """
    map_df = _twosides_map_frame(rxnorm_to_cid)
    batches = pl.scan_csv(path, infer_schema_length=0).collect_batches(
        chunk_size=batch_size
    )
    total = 0
    for df in batches:
        rows = _transform_twosides_batch(df, map_df)
        total += len(rows)
        yield from rows
    logger.info("Streamed TWOSIDES: %d interaction rows", total)


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
