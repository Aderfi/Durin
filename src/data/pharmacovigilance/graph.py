"""Neo4j graph backing store for the pharmacovigilance datasets.

Replaces the former SQLite layer (``db.py``). Owns three things: the driver /
connection factory, the one-time schema (uniqueness constraints), and the Cypher
the runtime store uses to read side effects / interactions by CID. Bulk loading
is done offline by ``scripts/build_pharmacovigilance.py`` via
``neo4j-admin database import`` — this module never writes fact data.

Graph model
    (:Drug {cid, name})
    (:AdverseEffect {code, coding_system, meddra_pt})
    (:Mechanism {key, mechanism, action_type})

    (:Drug)-[:HAS_SIDE_EFFECT {name, frequency, source, source_id}]->(:AdverseEffect)
    (:Drug)-[:INTERACTS_WITH {mechanism, conditions, prr, source, source_id}]-(:Drug)
    (:Drug)-[:HAS_MECHANISM {source, source_id}]->(:Mechanism)

``name`` lives on the HAS_SIDE_EFFECT edge (not the AdverseEffect node) because
for ``LLM_NORMALIZED`` effects it is the original free text, distinct per drug,
while the shared node is keyed by ``code``. An AdverseEffect's ``code`` is a UMLS
CUI (``coding_system="UMLS_CUI"``, from SIDER, which has no numeric MedDRA code)
or a numeric MedDRA code (``coding_system="MEDDRA"``, from the openFDA/LLM
normalizer). INTERACTS_WITH is a single de-duplicated edge per unordered drug
pair, read undirected.
"""

from __future__ import annotations

from neo4j import Driver, GraphDatabase

from src.config import Neo4jConfig, neo4j_config

# Node labels / relationship types — single source of truth, shared with the ETL.
DRUG = "Drug"
ADVERSE_EFFECT = "AdverseEffect"
MECHANISM = "Mechanism"
HAS_SIDE_EFFECT = "HAS_SIDE_EFFECT"
INTERACTS_WITH = "INTERACTS_WITH"
HAS_MECHANISM = "HAS_MECHANISM"

# One-time schema: single-property uniqueness constraints (idempotent).
# Community edition supports single-property uniqueness only (no composite NODE
# KEY), so Mechanism is keyed by a synthetic ``key`` = "mechanism||action_type".
CONSTRAINTS: tuple[str, ...] = (
    f"CREATE CONSTRAINT drug_cid IF NOT EXISTS FOR (d:{DRUG}) REQUIRE d.cid IS UNIQUE",
    f"CREATE CONSTRAINT adverse_effect_code IF NOT EXISTS "
    f"FOR (e:{ADVERSE_EFFECT}) REQUIRE e.code IS UNIQUE",
    f"CREATE CONSTRAINT mechanism_key IF NOT EXISTS "
    f"FOR (m:{MECHANISM}) REQUIRE m.key IS UNIQUE",
)

# Runtime reads. Each RETURN reproduces the dict shape the old SQLite rows had,
# so enrichment.py's model assembly is unchanged.
SIDE_EFFECTS_BY_CID = f"""
MATCH (d:{DRUG} {{cid: $cid}})-[r:{HAS_SIDE_EFFECT}]->(e:{ADVERSE_EFFECT})
RETURN r.name AS name, e.meddra_pt AS meddra_pt,
       CASE WHEN e.coding_system = 'MEDDRA' THEN e.code END AS meddra_code,
       CASE WHEN e.coding_system = 'UMLS_CUI' THEN e.code END AS umls_cui,
       r.frequency AS frequency, r.source AS source, r.source_id AS source_id
"""

INTERACTIONS_BY_CID = f"""
MATCH (d:{DRUG} {{cid: $cid}})-[r:{INTERACTS_WITH}]-(o:{DRUG})
RETURN o.cid AS interacting_cid, o.name AS interacting_name,
       r.mechanism AS mechanism, r.source AS source, r.source_id AS source_id
"""


def driver(config: Neo4jConfig | None = None) -> Driver:
    """Open a Neo4j driver from config (env-backed by default)."""
    cfg = config or neo4j_config()
    return GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))


def setup_constraints(drv: Driver, database: str) -> None:
    """Create the node-key uniqueness constraints (idempotent)."""
    with drv.session(database=database) as session:
        for statement in CONSTRAINTS:
            session.run(statement)
