"""Assemble SideEffect/Interaction models from the pharmacovigilance graph.

Queries the local Neo4j graph by CID and builds validated Pydantic models with
mandatory provenance. Models only validate; this module does the I/O and
assembly. Querying by an indexed ``:Drug(cid)`` means the interaction subgraph is
never loaded whole into memory.
"""

from __future__ import annotations

from src.config import Neo4jConfig, neo4j_config
from src.data.pharmacovigilance import graph
from src.data.schemas import Drug, Interaction, Provenance, SideEffect
from src.data.sources import derive_severity
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PharmacovigilanceStore:
    """Pharmacovigilance graph in Neo4j, assembling validated models by CID."""

    def __init__(self, config: Neo4jConfig | None = None) -> None:
        self._config = config or neo4j_config()
        self._driver = graph.driver(self._config)
        self._database = self._config.database

    def close(self) -> None:
        """Close the underlying Neo4j driver (releases connections)."""
        self._driver.close()

    def __enter__(self) -> PharmacovigilanceStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _assemble_effect(raw: dict) -> SideEffect:
        """Assemble one validated SideEffect with derived severity + provenance."""
        code = raw.get("meddra_code")
        severity, derived = derive_severity(code, is_serious=False)
        return SideEffect(
            name=raw["name"],
            meddra_pt=raw.get("meddra_pt"),
            meddra_code=code,
            umls_cui=raw.get("umls_cui"),
            severity=severity,
            severity_derived=derived,
            frequency=raw.get("frequency"),
            provenance=Provenance(source=raw["source"], source_id=raw.get("source_id")),
        )

    def _rows(self, query: str, cid: int) -> list[dict]:
        with self._driver.session(database=self._database) as session:
            result = session.run(query, cid=cid)
            return [record.data() for record in result]

    def side_effects(self, cid: int) -> list[SideEffect]:
        """Return assembled SideEffect models for a CID (empty if unknown).

        Merges Tier 1 SIDER effects with offline-coded openFDA effects
        (``source="LLM_NORMALIZED"``) — both are HAS_SIDE_EFFECT edges.
        """
        rows = self._rows(graph.SIDE_EFFECTS_BY_CID, cid)
        return [self._assemble_effect(raw) for raw in rows]

    def interactions(self, cid: int) -> list[Interaction]:
        """Return assembled Interaction models for a CID (empty if unknown).

        The INTERACTS_WITH edge is stored once per unordered pair and matched
        undirected, so an interaction is found from either endpoint.
        """
        out: list[Interaction] = []
        for raw in self._rows(graph.INTERACTIONS_BY_CID, cid):
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
