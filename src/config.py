"""Runtime configuration read from the environment.

Single seam for the Neo4j graph-database coordinates so nothing hardcodes a bolt
URL or credentials. Community edition hosts exactly one user database (``neo4j``),
which is therefore the default target.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Neo4jConfig:
    """Connection coordinates for the local Neo4j server."""

    uri: str
    user: str
    password: str
    database: str


def neo4j_config() -> Neo4jConfig:
    """Build the Neo4j config from ``NEO4J_*`` env vars (with local defaults)."""
    return Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "neo4j"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
