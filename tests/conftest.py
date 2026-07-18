"""Shared pytest fixtures.

The pharmacovigilance store is now graph-backed, so its tests need a real Neo4j.
We spin up an ephemeral container (``testcontainers[neo4j]``) once per session so
tests never touch the single local ``neo4j`` database. If Docker or
testcontainers is unavailable, the graph-backed tests skip cleanly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from src.config import Neo4jConfig
from src.data.enrichment import PharmacovigilanceStore
from src.data.pharmacovigilance import graph

try:
    from testcontainers.neo4j import Neo4jContainer

    _IMPORT_ERR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    Neo4jContainer = None  # type: ignore[assignment]
    _IMPORT_ERR = exc

_NEO4J_IMAGE = "neo4j:5-community"


@pytest.fixture(scope="session")
def neo4j_config() -> Neo4jConfig:
    """Start an ephemeral Neo4j and yield its connection config (session-scoped)."""
    if Neo4jContainer is None:
        pytest.skip(f"testcontainers unavailable: {_IMPORT_ERR}")
    try:
        container = Neo4jContainer(_NEO4J_IMAGE)
        container.start()
    except Exception as exc:  # pragma: no cover - Docker not available
        pytest.skip(f"cannot start Neo4j container (Docker running?): {exc}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(7687)
        yield Neo4jConfig(
            uri=f"bolt://{host}:{port}",
            user=getattr(container, "username", "neo4j"),
            password=getattr(container, "password", "password"),
            database="neo4j",
        )
    finally:
        container.stop()


def _seed(cfg: Neo4jConfig, **datasets: Mapping[str | int, list[dict]]) -> None:
    """Load ``{cid: [record]}`` datasets into the graph, mirroring the old build_db.

    Accepts ``sider``, ``openfda``, ``twosides``, ``chembl`` — the same keyword
    shape the SQLite ``build_db`` used, so tests port almost verbatim.
    """
    drv = graph.driver(cfg)
    try:
        with drv.session(database=cfg.database) as s:
            for table in ("sider", "openfda"):
                for cid, rows in (datasets.get(table) or {}).items():
                    for r in rows:
                        if r.get("meddra_code"):
                            code, system = r["meddra_code"], "MEDDRA"
                        else:
                            code, system = r.get("umls_cui"), "UMLS_CUI"
                        s.run(
                            f"MERGE (d:{graph.DRUG} {{cid: $cid}}) "
                            f"MERGE (e:{graph.ADVERSE_EFFECT} {{code: $code}}) "
                            f"SET e.meddra_pt = $pt, e.coding_system = $system "
                            f"CREATE (d)-[x:{graph.HAS_SIDE_EFFECT}]->(e) "
                            f"SET x.name = $name, x.frequency = $freq, "
                            f"    x.source = $source, x.source_id = $sid",
                            cid=int(cid),
                            code=code,
                            system=system,
                            pt=r.get("meddra_pt"),
                            name=r["name"],
                            freq=r.get("frequency"),
                            source=r["source"],
                            sid=r.get("source_id"),
                        )
            for cid, rows in (datasets.get("twosides") or {}).items():
                for r in rows:
                    s.run(
                        f"MERGE (a:{graph.DRUG} {{cid: $cid}}) "
                        f"MERGE (b:{graph.DRUG} {{cid: $other}}) "
                        f"FOREACH (_ IN "
                        f"  CASE WHEN $oname IS NULL THEN [] ELSE [1] END | "
                        f"  SET b.name = $oname) "
                        f"CREATE (a)-[x:{graph.INTERACTS_WITH}]->(b) "
                        f"SET x.mechanism = $mech, x.source = $source, "
                        f"    x.source_id = $sid",
                        cid=int(cid),
                        other=r["interacting_cid"],
                        oname=r.get("interacting_name"),
                        mech=r["mechanism"],
                        source=r["source"],
                        sid=r.get("source_id"),
                    )
            for cid, rows in (datasets.get("chembl") or {}).items():
                for r in rows:
                    key = f"{r['mechanism']}||{r.get('action_type') or ''}"
                    s.run(
                        f"MERGE (d:{graph.DRUG} {{cid: $cid}}) "
                        f"MERGE (m:{graph.MECHANISM} {{key: $key}}) "
                        f"SET m.mechanism = $mech, m.action_type = $action "
                        f"CREATE (d)-[x:{graph.HAS_MECHANISM}]->(m) "
                        f"SET x.source = $source, x.source_id = $sid",
                        cid=int(cid),
                        key=key,
                        mech=r["mechanism"],
                        action=r.get("action_type"),
                        source=r["source"],
                        sid=r.get("source_id"),
                    )
    finally:
        drv.close()


@pytest.fixture
def pv_graph(
    neo4j_config: Neo4jConfig,
) -> tuple[Callable[..., PharmacovigilanceStore], Neo4jConfig]:
    """Wipe the graph, apply constraints, and return a seed->store factory.

    Usage::

        def test_x(pv_graph):
            make_store, cfg = pv_graph
            store = make_store(sider={...}, twosides={...})
            store.side_effects(2244)
    """
    drv = graph.driver(neo4j_config)
    try:
        with drv.session(database=neo4j_config.database) as s:
            s.run("MATCH (n) DETACH DELETE n")
            for statement in graph.CONSTRAINTS:
                s.run(statement)
    finally:
        drv.close()

    stores: list[PharmacovigilanceStore] = []

    def make_store(
        **datasets: Mapping[str | int, list[dict]],
    ) -> PharmacovigilanceStore:
        _seed(neo4j_config, **datasets)
        store = PharmacovigilanceStore(neo4j_config)
        stores.append(store)
        return store

    yield make_store, neo4j_config
    for store in stores:
        store.close()
