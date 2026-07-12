"""Assemble SideEffect/Interaction models from Tier 1 datasets (+ Tier 2 openFDA).

Loads the local per-CID JSON datasets and builds validated Pydantic models with
mandatory provenance. Tier 2 (openFDA) fills gaps on demand when a cache dir is
given. Models only validate; this module does the I/O and assembly.
"""

import json
from pathlib import Path

from src.data.schemas import Drug, Interaction, Provenance, SideEffect
from src.data.sources import derive_severity
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _load(path: Path) -> dict[str, list[dict]]:
    """Load a per-CID JSON dataset, or an empty dict if the file is missing."""
    if not path.exists():
        logger.warning("Tier 1 dataset missing: %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class PharmacovigilanceStore:
    """Local Tier 1 datasets keyed by CID, assembling validated models."""

    def __init__(self, data_dir: Path, cache_dir: Path | None = None) -> None:
        self._effects = _load(data_dir / "sider_effects.json")
        self._openfda = _load(data_dir / "openfda_effects.json")
        self._interactions = _load(data_dir / "twosides_ddi.json")
        self._moa = _load(data_dir / "chembl_moa.json")
        self._cache_dir = cache_dir

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

    def side_effects(self, cid: int) -> list[SideEffect]:
        """Return assembled SideEffect models for a CID (empty if unknown).

        Merges Tier 1 SIDER effects with offline-coded openFDA effects
        (``source="LLM_NORMALIZED"``); runtime reads the pre-built JSON only.
        """
        key = str(cid)
        rows = self._effects.get(key, []) + self._openfda.get(key, [])
        return [self._assemble_effect(raw) for raw in rows]

    def interactions(self, cid: int) -> list[Interaction]:
        """Return assembled Interaction models for a CID (empty if unknown)."""
        out: list[Interaction] = []
        for raw in self._interactions.get(str(cid), []):
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
