"""Assemble SideEffect/Interaction models from the pharmacovigilance SQLite DB.

Queries the local SQLite datasets by CID and builds validated Pydantic models
with mandatory provenance. Models only validate; this module does the I/O and
assembly. Querying by CID (indexed) means TWOSIDES never has to be loaded whole
into memory.
"""

from pathlib import Path

from src.data.pharmacovigilance import db
from src.data.schemas import Drug, Interaction, Provenance, SideEffect
from src.data.sources import derive_severity
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PharmacovigilanceStore:
    """Pharmacovigilance datasets in SQLite, assembling validated models by CID."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = db.connect(db_path)

    @staticmethod
    def _assemble_effect(raw: dict) -> SideEffect:
        """Assemble one validated SideEffect with derived severity + provenance."""
        code = raw.get("meddra_code")
        severity, derived = derive_severity(code, is_serious=False)
        return SideEffect(
            name=raw["name"],
            meddra_pt=raw.get("meddra_pt"),
            meddra_code=code,
            severity=severity,
            severity_derived=derived,
            frequency=raw.get("frequency"),
            provenance=Provenance(
                source=raw["source"], source_id=raw.get("source_id")
            ),
        )

    def _rows(self, table: str, cid: int) -> list[dict]:
        cursor = self._conn.execute(f"SELECT * FROM {table} WHERE cid = ?", (cid,))
        return [dict(row) for row in cursor.fetchall()]

    def side_effects(self, cid: int) -> list[SideEffect]:
        """Return assembled SideEffect models for a CID (empty if unknown).

        Merges Tier 1 SIDER effects with offline-coded openFDA effects
        (``source="LLM_NORMALIZED"``).
        """
        rows = self._rows("sider_effects", cid) + self._rows("openfda_effects", cid)
        return [self._assemble_effect(raw) for raw in rows]

    def interactions(self, cid: int) -> list[Interaction]:
        """Return assembled Interaction models for a CID (empty if unknown)."""
        out: list[Interaction] = []
        for raw in self._rows("twosides_ddi", cid):
            # Guarantee drug identity (require_drug_identity): use the resolved
            # name if the ETL provided one, else a deterministic CID-based label.
            name = raw.get("interacting_name") or f"CID {raw['interacting_cid']}"
            out.append(
                Interaction(
                    interacting_drug=name,
                    interacting_cid=raw.get("interacting_cid"),
                    interaction_type="PD",
                    mechanism=raw["mechanism"],
                    provenance=Provenance(
                        source=raw["source"], source_id=raw.get("source_id")
                    ),
                )
            )
        return out


def enrich_drug(drug: Drug, store: PharmacovigilanceStore) -> Drug:
    """Return a copy of ``drug`` with side effects and interactions populated."""
    return drug.model_copy(
        update={
            "side_effects": store.side_effects(drug.cid),
            "interactions": store.interactions(drug.cid),
        }
    )
