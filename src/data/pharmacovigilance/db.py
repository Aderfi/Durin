"""SQLite backing store for the pharmacovigilance datasets.

One database file holds the four datasets that used to be separate JSON files,
one table each, keyed by PubChem ``cid`` (indexed). Each table's columns mirror
the record shape of the old ``{cid: [record, ...]}`` JSON, so the runtime store
assembles the same models — it just queries by ``cid`` instead of loading a whole
JSON into memory. This is what lets TWOSIDES (tens of millions of rows) scale on
both the build side (streaming inserts) and the runtime side (indexed lookups).
"""

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

# Column order per table. `cid` first; the rest mirror each dataset's JSON record.
EFFECT_COLUMNS = [
    "cid",
    "name",
    "meddra_pt",
    "meddra_code",
    "frequency",
    "source",
    "source_id",
]
INTERACTION_COLUMNS = [
    "cid",
    "interacting_cid",
    "interacting_name",
    "mechanism",
    "meddra_pt",
    "meddra_code",
    "source",
    "source_id",
]
MOA_COLUMNS = ["cid", "mechanism", "action_type", "source", "source_id"]

# Table -> column order. sider_effects and openfda_effects share the effect shape.
TABLES: dict[str, list[str]] = {
    "sider_effects": EFFECT_COLUMNS,
    "openfda_effects": EFFECT_COLUMNS,
    "twosides_ddi": INTERACTION_COLUMNS,
    "chembl_moa": MOA_COLUMNS,
}

# Columns stored as INTEGER (identity); everything else is TEXT (or NULL).
_INTEGER_COLUMNS = {"cid", "interacting_cid"}


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with ``sqlite3.Row`` so rows read back as mappings."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the four dataset tables and their per-``cid`` indexes (idempotent)."""
    for table, columns in TABLES.items():
        coldefs = ", ".join(
            f"{c} INTEGER" if c in _INTEGER_COLUMNS else f"{c} TEXT" for c in columns
        )
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({coldefs})")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_cid ON {table}(cid)")
    conn.commit()


def insert_rows(
    conn: sqlite3.Connection, table: str, rows: Iterable[Mapping]
) -> None:
    """Insert an iterable of record mappings into ``table`` (streaming-friendly)."""
    columns = TABLES[table]
    placeholders = ", ".join("?" * len(columns))
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(statement, ([row.get(c) for c in columns] for row in rows))


def insert_by_cid(
    conn: sqlite3.Connection,
    table: str,
    by_cid: Mapping[str | int, list[dict]],
) -> None:
    """Insert a ``{cid: [record, ...]}`` mapping, flattening ``cid`` into each row."""

    def _rows() -> Iterable[dict]:
        for cid, records in by_cid.items():
            for record in records:
                yield {"cid": int(cid), **record}

    insert_rows(conn, table, _rows())


def build_db(
    db_path: str | Path,
    *,
    sider: Mapping[str | int, list[dict]] | None = None,
    openfda: Mapping[str | int, list[dict]] | None = None,
    twosides: Mapping[str | int, list[dict]] | None = None,
    chembl: Mapping[str | int, list[dict]] | None = None,
) -> Path:
    """Create a fresh DB and load the given ``{cid: [record]}`` datasets.

    Convenience for small datasets and tests; TWOSIDES is streamed separately by
    the ETL rather than passed here as an in-memory mapping.
    """
    conn = connect(db_path)
    try:
        create_schema(conn)
        for table, data in (
            ("sider_effects", sider),
            ("openfda_effects", openfda),
            ("twosides_ddi", twosides),
            ("chembl_moa", chembl),
        ):
            if data:
                insert_by_cid(conn, table, data)
        conn.commit()
    finally:
        conn.close()
    return Path(db_path)
