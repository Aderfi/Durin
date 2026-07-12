"""One-off: recover ChEMBL MoA rows missing from the pharmacovigilance DB.

Some ChEMBL molecules with a mechanism of action were dropped during the build
because their ChEMBL id is absent from the UniChem ChEMBL->PubChem map
(``src1src22``) -- a version-lag gap. This script recovers the *small-molecule*
ones by structure:

    ChEMBL id  ->  standard InChI (from the ChEMBL SQLite dump)  ->  PubChem CID

matching on the full InChI (via PUG-REST POST) rather than the InChIKey, because
PubChem canonicalizes structures and often indexes a molecule under a different
salt/stereo InChIKey -- so InChIKey lookups miss where the InChI resolves. The
recovered MoA rows are inserted into the existing ``chembl_moa`` table.

Only molecules that HAVE a structure (InChI) are recoverable. Molecules without
one are biologics (antibody-drug conjugates, peptides; ``structure_type = 'SEQ'``)
that have no PubChem compound at all -- nothing can map them, so they are reported
and skipped.

Circumstantial: building UniChem and ChEMBL from matching releases avoids needing
this. Run it AFTER the main build finishes (it writes to the same SQLite file).

Example:
    python recover_chembl_cids.py \\
      --moa-csv tmp/chembl_moa.csv \\
      --chembl-db tmp/chembl_37/chembl_37_sqlite/chembl_37.db \\
      --unichem tmp/src1src22.txt \\
      --db src/data/pharmacovigilance/pharmacovigilance.db
"""

import argparse
import sqlite3
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

from src.data.pharmacovigilance import db
from src.utils.logging import get_logger

logger = get_logger(__name__)

_INCHI_CID = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchi/cids/JSON"
_TIMEOUT = 30  # seconds
_THROTTLE = 0.2  # seconds between requests (<=5 req/s PUG limit)
_SQL_CHUNK = 900  # keep under SQLite's parameter limit for IN (...)


def _load_unichem_keys(path: Path) -> set[str]:
    """ChEMBL ids already covered by the UniChem map (only the mapped side)."""
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip().isdigit():
            keys.add(parts[0].strip())
    return keys


def _missing_chembl_ids(moa_csv: Path, unichem_keys: set[str]) -> list[str]:
    """ChEMBL ids present in the MoA CSV but absent from the UniChem map."""
    ids = pl.read_csv(moa_csv).get_column("molecule_chembl_id").unique().to_list()
    return [i for i in ids if i not in unichem_keys]


def _inchis_for(chembl_db: Path, chembl_ids: list[str]) -> dict[str, str]:
    """Map each ChEMBL id to its standard InChI from the dump (missing -> absent)."""
    conn = sqlite3.connect(chembl_db)
    mapping: dict[str, str] = {}
    try:
        for start in range(0, len(chembl_ids), _SQL_CHUNK):
            chunk = chembl_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT d.chembl_id, cs.standard_inchi "
                "FROM molecule_dictionary d "
                "JOIN compound_structures cs ON d.molregno = cs.molregno "
                f"WHERE d.chembl_id IN ({placeholders}) "
                "AND cs.standard_inchi IS NOT NULL",
                chunk,
            ).fetchall()
            mapping.update({chembl_id: inchi for chembl_id, inchi in rows})
    finally:
        conn.close()
    return mapping


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _inchi_to_cid(inchi: str) -> int | None:
    """Resolve a standard InChI to its first PubChem CID, or None if not found."""
    resp = requests.post(_INCHI_CID, data={"inchi": inchi}, timeout=_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    cids = resp.json().get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def _resolve_cids(inchis: dict[str, str]) -> dict[str, int]:
    """Resolve {chembl_id: inchi} to {chembl_id: cid}, dropping unresolved."""
    resolved: dict[str, int] = {}
    for chembl_id, inchi in inchis.items():
        try:
            cid = _inchi_to_cid(inchi)
        except requests.RequestException:
            logger.error("PubChem request failed for %s", chembl_id)
            cid = None
        if cid is not None:
            resolved[chembl_id] = cid
        else:
            logger.debug("No CID for %s", chembl_id)
        time.sleep(_THROTTLE)
    return resolved


def _moa_rows(moa_csv: Path, chembl_to_cid: dict[str, int]) -> dict[str, list[dict]]:
    """Build {cid: [chembl_moa record]} for the recovered ChEMBL ids only."""
    frame = pl.read_csv(moa_csv).filter(
        pl.col("molecule_chembl_id").is_in(list(chembl_to_cid))
    )
    by_cid: dict[str, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        chembl_id = row["molecule_chembl_id"]
        cid = chembl_to_cid[chembl_id]
        by_cid.setdefault(str(cid), []).append(
            {
                "mechanism": row["mechanism_of_action"],
                "action_type": row["action_type"],
                "source": "ChEMBL",
                "source_id": chembl_id,
            }
        )
    return by_cid


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover missing ChEMBL MoA CIDs via InChIKey and patch the DB."
    )
    parser.add_argument("--moa-csv", type=Path, required=True)
    parser.add_argument("--chembl-db", type=Path, required=True)
    parser.add_argument("--unichem", type=Path, required=True)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("src/data/pharmacovigilance/pharmacovigilance.db"),
    )
    args = parser.parse_args()

    unichem_keys = _load_unichem_keys(args.unichem)
    missing = _missing_chembl_ids(args.moa_csv, unichem_keys)
    inchis = _inchis_for(args.chembl_db, missing)
    logger.info(
        "Missing %d ChEMBL ids: %d have a structure (recoverable), "
        "%d are biologics with no structure (unmappable)",
        len(missing),
        len(inchis),
        len(missing) - len(inchis),
    )

    chembl_to_cid = _resolve_cids(inchis)
    by_cid = _moa_rows(args.moa_csv, chembl_to_cid)
    inserted = sum(len(rows) for rows in by_cid.values())

    conn = db.connect(args.db)
    try:
        db.insert_by_cid(conn, "chembl_moa", by_cid)
        conn.commit()
    finally:
        conn.close()
    logger.info(
        "Recovered %d CIDs, inserted %d chembl_moa rows into %s",
        len(chembl_to_cid),
        inserted,
        args.db,
    )


if __name__ == "__main__":
    main()
