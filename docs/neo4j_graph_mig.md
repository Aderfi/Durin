# Moving the pharmacovigilance store from SQLite to Neo4j

The pharmacovigilance data — a drug's side effects, its interactions with
other drugs, its mechanism of action — used to live in a single SQLite file
(`db.py`, four flat tables keyed by PubChem CID, no primary/foreign keys). It
was replaced with Neo4j. `db.py` is gone; there's no dual-backend or
compatibility shim, it's a straight cutover.

## Why

The data is relational in the graph sense, not the table sense: a drug
connects to many effects, many other drugs (interactions), many mechanisms.
SQLite modeled this as flat rows re-keyed by `cid` on every table, with no
constraints enforcing referential integrity. The interaction table alone held
89,134,420 rows (TWOSIDES), stored bidirectionally — both `A interacts_with B`
and `B interacts_with A` as separate rows. Neo4j stores that as one edge,
queried undirected.

The migration kept the store's public surface (`PharmacovigilanceStore`,
`enrich_drug`) unchanged on purpose, so `repository.get_enriched_drug` and the
risk evaluators that consume `drug.side_effects`/`drug.interactions` needed no
changes — they never touched SQL directly, so swapping what's behind the
store was low-risk by construction.

## The graph model

Three node labels, three relationship types, defined in
`src/data/pharmacovigilance/graph.py`:

```
(:Drug {cid, name})
(:AdverseEffect {code, coding_system, meddra_pt})
(:Mechanism {key, mechanism, action_type})

(:Drug)-[:HAS_SIDE_EFFECT {name, frequency, source, source_id}]->(:AdverseEffect)
(:Drug)-[:INTERACTS_WITH {mechanism, conditions, prr, source, source_id}]-(:Drug)
(:Drug)-[:HAS_MECHANISM {source, source_id}]->(:Mechanism)
```

A couple of details worth calling out because they're not obvious from the
model alone:

- `name` lives on the `HAS_SIDE_EFFECT` edge, not on `AdverseEffect`. For
  `LLM_NORMALIZED` effects, that name is the original free text — different
  per drug — while the `AdverseEffect` node itself is shared and keyed by
  `code`.
- `AdverseEffect.code` is either a UMLS CUI (`coding_system="UMLS_CUI"`, from
  SIDER) or a numeric MedDRA code (`coding_system="MEDDRA"`, from openFDA via
  the LLM normalizer). This exists because of a coding bug caught during the
  migration: SIDER's fifth column is actually a UMLS CUI, not a MedDRA code as
  the original SQLite schema assumed. `SideEffect.umls_cui` was added to the
  domain model to carry it correctly instead of silently mislabeling it.
- `Mechanism` is keyed by a synthetic `key` = `"mechanism||action_type"`, not
  a composite key — Neo4j Community edition only supports single-property
  uniqueness constraints, no composite `NODE KEY`.
- `INTERACTS_WITH` is one edge per unordered drug pair, matched undirected at
  read time (`MATCH (d)-[r:INTERACTS_WITH]-(o)`, no arrow direction) — this is
  what collapses the old bidirectional row storage.

ATC and ICD-10 codes stay outside the graph entirely, as local JSON lookups —
they were never in SQLite either, and nothing about the migration changed
that.

## Loading the data

`scripts/build_pharmacovigilance.py` still parses the same source files it
always did; the difference is what it emits. Instead of writing to SQLite
directly, it writes CSVs (`tmp/neo4j_import/`) shaped for
`neo4j-admin database import full` — a bulk loader that reads CSVs into an
empty database, which is what makes loading 89 million source rows (deduped
down to 176,624 `INTERACTS_WITH` edges) practical. Transactional inserts at
that volume were never seriously considered.

That import step is deliberately not automated. It has to run against a
stopped Neo4j service, needs `sudo`, and overwrites the single Community-edition
database with no undo. The ETL script stages the CSVs and prints the exact
`neo4j-admin` command; running it is a manual, conscious action. Snakemake's
pipeline (see the orchestration doc) stops at the same point for the same
reason.

Because Community edition hosts exactly one user database, `NEO4J_DATABASE`
defaults to `"neo4j"` — there's no named per-project database to isolate
into. `src/config.py` centralizes the connection coordinates
(`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE`, all read from the
environment, defaulting to `bolt://localhost:7687`).

## Tests

`tests/conftest.py` spins up an ephemeral Neo4j container per test session via
`testcontainers[neo4j]`, so the test suite never touches the one real local
database. If Docker isn't available, those tests skip instead of failing —
this is the same skip behavior visible in the CI-oriented Docker image (83
passed, 9 skipped without a mounted Docker socket).

## What's still manual

The `neo4j-admin` import itself, as covered above — staged, never run
automatically. `recover_chembl_cids.py`, a one-off script that patches missing
ChEMBL mechanism data into an already-imported live database via a Cypher
`MERGE` upsert, also stays outside any automated pipeline — it assumes the
graph already exists and is live, which is a different precondition than
everything upstream of the import.
