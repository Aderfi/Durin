# Pharmacovigilance Store Migration: SQLite → Neo4j Graph Database

**Date:** 2026-07-18
**Status:** ✅ Complete — imported into local Neo4j and live-verified end-to-end
**Type:** Architecture / storage-backend migration (no backward compatibility)

> **Live import verified (2026-07-18):** `neo4j-admin database import full neo4j`
> loaded 11,125 nodes + 345,045 relationships in ~4s. Against the running server:
> connectivity OK, constraints applied, counts exact (5,681 Drug · 4,251
> AdverseEffect · 1,193 Mechanism · 163,206 HAS_SIDE_EFFECT · 176,624
> INTERACTS_WITH · 5,215 HAS_MECHANISM). Read path confirmed on real data:
> `side_effects(85)` → 73 (SIDER `umls_cui` set, `meddra_code` null, no
> ValidationError); `interactions(174)` → 18 (undirected single-edge match, real
> interacting-drug names). DB password: `durin-local` (set `NEO4J_PASSWORD`).

> **Implementation status (2026-07-18)**
> - Code migrated: `src/config.py` (new), `src/data/pharmacovigilance/graph.py`
>   (replaces deleted `db.py`), `src/data/enrichment.py` (Neo4j store, identical
>   public surface), `scripts/build_pharmacovigilance.py` (emits neo4j-admin
>   CSVs, streaming TWOSIDES dedup), `recover_chembl_cids.py` (Cypher MERGE
>   upsert). Dep added: `neo4j` 6.2.0 (runtime), `testcontainers[neo4j]` (dev).
> - Community fix applied: single-property uniqueness only — Mechanism keyed by
>   synthetic `key` (no composite NODE KEY).
> - **SIDER coding fix:** SIDER has no numeric MedDRA code (its 5th column is a
>   UMLS CUI). AdverseEffect is now keyed by a generic `code` + `coding_system`;
>   `SideEffect.umls_cui` added; `parse_sider` emits `umls_cui`, not `meddra_code`.
> - **Tests: 92 passed.** Graph-backed store tests run end-to-end against an
>   ephemeral Neo4j 5 (testcontainers); ETL CSV emission + dedup verified on
>   fixtures. Ruff clean on all migrated files.
> - **CSVs emitted + verified from real `tmp/` sources** (writes only to
>   `tmp/neo4j_import/`): 5,681 drugs · 4,251 effects (all `UMLS_CUI`) · 1,193
>   mechanisms · 163,206 SE edges · 5,215 MoA edges · **176,624** deduped
>   INTERACTS_WITH edges (from 89M directional rows). `interacts_with.csv` ≈ 1.6 GB
>   (condition/PRR lists on each edge).
> - **Remaining manual step (HELD, per decision):** run `neo4j-admin database
>   import full neo4j ...` against the STOPPED system service (needs `sudo`,
>   initial-password bootstrap). Not auto-run — it overwrites the single local
>   `neo4j` database. CSVs are already staged in `tmp/neo4j_import/`.

---

## 1. Context

The pharmacovigilance / drug-interaction dataset (side effects, drug–drug
interactions, mechanisms of action) is currently built into a single **SQLite**
file (`src/data/pharmacovigilance/pharmacovigilance.db`) by two offline ETL
scripts and read at runtime by one CID-keyed store.

The data is inherently a **graph** — Drug ↔ AdverseEffect, Drug ↔ Drug
interactions, Drug ↔ Mechanism — so it maps far more naturally onto a local
**Neo4j graph database**.

**Goal:** keep the *same workflow and the same source files*, but change the
**output** from SQLite tables to a Neo4j graph, and migrate **every** consumer
(read + build + tests) to Neo4j.

**Explicitly no backward compatibility** — SQLite is removed entirely. No
dual-backend, no compatibility shim, no fallback.

### Confirmed decisions

| Topic | Decision |
|---|---|
| **Client** | Official `neo4j` Python driver (installed in the uv env). Server = **Neo4j 2026.06.0 Community**, system-wide (`/usr/bin/neo4j*`), at `bolt://localhost:7687`. |
| **Bulk load (~89M TWOSIDES rows)** | `neo4j-admin database import full` (offline, into an empty DB). ETL emits header + data CSVs; the importer loads them. Orders of magnitude faster than transactional inserts. |

### Environment-specific deltas (confirmed 2026-07-18)

- **Community edition → single user database.** The graph IS the default `neo4j`
  DB (Community cannot host a second named user DB). `NEO4J_DATABASE` default =
  `"neo4j"`; import targets `neo4j` with `--overwrite-destination`.
- **Import command** (calver syntax, run with the **server stopped**):
  `neo4j-admin database import full neo4j --nodes=Drug=drugs_header.csv,drugs.csv
  --nodes=AdverseEffect=... --nodes=Mechanism=... --relationships=HAS_SIDE_EFFECT=...
  --relationships=INTERACTS_WITH=... --relationships=HAS_MECHANISM=...
  --id-type=string --overwrite-destination`. CSVs use per-group ID spaces
  (`:ID(Drug)`, `:ID(Effect)`, `:ID(Mechanism)`) — 3 distinct node keyspaces.
- **Auth bootstrap:** `neo4j-admin dbms set-initial-password <pw>` once; runtime +
  tests read `NEO4J_PASSWORD` from env.
- **Server lifecycle:** import needs the server stopped; constraint-setup +
  runtime need it started (`sudo systemctl start neo4j`). ETL emits CSVs and
  prints/runs the import; start/stop is an ops step.
- **Tests:** keep `testcontainers[neo4j]` (Docker present) — ephemeral isolated
  instance so tests never wipe the single real local `neo4j` DB.
| **Interaction edges** | A single **de-duplicated** `INTERACTS_WITH` edge per unordered drug pair (collapses the current two-row bidirectional storage), queried undirected at runtime. |
| **ATC / ICD** | Stay as **node properties / JSON lookups**, never isolated nodes (they were never in SQLite either). Same "prefer property; create the shared node only when the code is a real shared entity" principle drives the model. |

---

## 2. Current-state map (what we are migrating)

The entire SQLite surface is **3 files** plus **3 tests**:

| File | Role |
|---|---|
| `src/data/pharmacovigilance/db.py` | SQLite layer — schema (4 tables), `connect`, `insert_*`, `build_db`. Uses stdlib `sqlite3` only. |
| `src/data/enrichment.py` | Runtime reader — `PharmacovigilanceStore` (single query `SELECT * FROM {table} WHERE cid = ?`), `enrich_drug`. |
| `scripts/build_pharmacovigilance.py` | Offline ETL writer — parses sources, streams 89M TWOSIDES rows in 500k batches. |
| `recover_chembl_cids.py` | One-off recovery — reads external ChEMBL SQLite dump, writes into our DB. |
| `tests/test_enrichment.py`, `tests/test_repository.py`, `tests/test_build_pharmacovigilance.py` | Coupled to `db.build_db` / `db.connect`. |

**Current SQLite schema** (4 flat tables, keyed/indexed by PubChem `cid`, no PK/FK/UNIQUE):

- `sider_effects` / `openfda_effects` — `cid, name, meddra_pt, meddra_code, frequency, source, source_id`
- `twosides_ddi` — `cid, interacting_cid, interacting_name, mechanism, meddra_pt, meddra_code, source, source_id` (~89M rows, stored bidirectionally)
- `chembl_moa` — `cid, mechanism, action_type, source, source_id`

**Live row counts:** `sider_effects` 489,618 · `openfda_effects` 0 · `twosides_ddi` 89,134,420 · `chembl_moa` 15,838.

**Downstream (unchanged by the migration):** `repository.get_enriched_drug` →
risk evaluators (`adverse_effect.py`, `drug_drug.py`) consume
`drug.side_effects` / `drug.interactions`. They never touch SQL, so keeping the
store's public surface identical means they need **no change**.

**Not in SQLite (separate JSON subsystem, out of scope):** ATC (`atc_codes.json`)
and ICD-10 (`icd10_codes.json`) catalogs.

---

## 3. Target graph model

### Nodes

- `(:Drug {cid, name})` — key = `cid` (unique constraint). ATC codes, when
  known, live as a property, **not** as separate nodes.
- `(:AdverseEffect {code, coding_system, meddra_pt})` — key = `code` (shared:
  many drugs point at the same effect → the graph payoff). `code` is a UMLS CUI
  (`coding_system="UMLS_CUI"`, from SIDER, which carries **no** numeric MedDRA
  code — the original `parse_sider` mislabeled SIDER's UMLS-CUI column as
  `meddra_code`) or a numeric MedDRA code (`coding_system="MEDDRA"`, from the
  openFDA/LLM normalizer). The two id spaces do not collide. `SideEffect` gained
  a `umls_cui` field; `meddra_code` stays null for SIDER effects.
- `(:Mechanism {key, mechanism, action_type})` — key = synthetic
  `key = "mechanism||action_type"` (single-prop unique; Community has no
  composite NODE KEY).

### Relationships

Provenance carried as edge properties (mirrors current `Provenance`: `source`,
`source_id`).

- `(:Drug)-[:HAS_SIDE_EFFECT {frequency, source, source_id}]->(:AdverseEffect)`
  — from `sider_effects` + `openfda_effects` (the `source` prop distinguishes
  SIDER vs `LLM_NORMALIZED`, preserving today's merge).
- `(:Drug)-[:INTERACTS_WITH {conditions, meddra_codes, prr, source, source_id}]->(:Drug)`
  — from `twosides_ddi`, **de-duplicated to one edge per pair**; per-condition
  detail aggregated into list properties.
- `(:Drug)-[:HAS_MECHANISM {source, source_id}]->(:Mechanism)` — from `chembl_moa`.

### Constraints (created once at setup)

`UNIQUE (Drug.cid)` · `UNIQUE (AdverseEffect.meddra_code)` ·
`UNIQUE (Mechanism.mechanism, .action_type)`.

---

## 4. Changes — file by file

### 4.1 New DB layer — `src/data/pharmacovigilance/graph.py` (replaces `db.py`)

- `driver()` / `GraphConnection` context manager wrapping
  `neo4j.GraphDatabase.driver(uri, auth=(user, pwd))` — the analogue of the old
  `db.connect()`. Reads connection from the config seam (§4.2).
- `setup_constraints(session)` — analogue of `create_schema()`: `CREATE
  CONSTRAINT ... IF NOT EXISTS` for the three node keys.
- Cypher query constants for the runtime store (side-effects-by-cid,
  interactions-by-cid).
- **Verify current `neo4j` driver API via find-docs** before writing
  (`execute_query()` vs session `run()` per current guidance).

### 4.2 Config seam (net-new — none exists today)

Add `src/config.py` exposing `NEO4J_URI` (default `bolt://localhost:7687`),
`NEO4J_USER` (`neo4j`), `NEO4J_PASSWORD`, `NEO4J_DATABASE` from env via
`os.getenv` (or `pydantic-settings` for validation — `pydantic` already a dep).
Replaces the hardcoded `--out` SQLite path + injected `db_path` constructor arg.

### 4.3 Runtime reader — `src/data/enrichment.py`

Rewrite `PharmacovigilanceStore` to be Neo4j-backed while keeping its **public
surface identical** (`side_effects(cid)`, `interactions(cid)`, `_assemble_effect`,
module-level `enrich_drug`):

- `__init__` opens a driver/session instead of `db.connect()`.
- `_rows()` → Cypher `MATCH` queries returning the same dict shape the
  assemblers already expect, so `_assemble_effect` + `derive_severity`
  (`src/data/sources.py:58`) and `Interaction` assembly stay unchanged.
- `interactions()` uses an **undirected** match so the single dedup edge is
  found from either endpoint.
- Add `close()` + context-manager support (driver sessions must be closed).

`repository.get_enriched_drug` needs **no logic change** — it stays
store-injected; only the store's backing type changes.

### 4.4 ETL rewrite — `scripts/build_pharmacovigilance.py`

Keep the CLI, the `BuildInputs` dataclass, and **reuse all source parsing**
(`parse_sider`, `parse_chembl_moa`, `iter_twosides_rows` in `src/data/sources.py`,
plus `_load_tsv_map`, `build_meddra_vocab`, `normalize_openfda_effects`).
Change only the **sink**:

- Replace `--out <db>` with `--import-dir <dir>` (the Neo4j server's `import/`
  folder or a staging dir).
- Dedup in Polars while streaming; write header + data CSVs: `drugs.csv`,
  `adverse_effects.csv`, `mechanisms.csv`, `has_side_effect.csv`,
  `interacts_with.csv` (pairs collapsed), `has_mechanism.csv`. Node CSVs use
  `:ID`/label headers; rel CSVs use `:START_ID`/`:END_ID`/`:TYPE`.
- Drug `:ID` set = union of all CIDs seen across sources (so every referenced
  code has a node — the "create the node if it doesn't exist" requirement).
- TWOSIDES stays streamed via `iter_twosides_rows` (never in RAM); dedup keyed on
  the unordered `(min, max)` CID pair, aggregating condition/PRR into list cols.
- `build_database()` → `build_import_csvs()` + a documented/subprocess
  `neo4j-admin database import full --nodes=... --relationships=... <db>`
  (DB must be stopped/empty for a full import), then a post-step opening the
  driver and running `setup_constraints`.

### 4.5 Port RxNorm map + recovery scripts

- `scripts/build_rxnorm_map.py` — **no change** (produces the RxNorm→CID TSV the
  ETL consumes; storage-backend-independent).
- `recover_chembl_cids.py` — the external ChEMBL SQLite dump stays SQLite (raw
  input, not our store), but repoint its **write side** to emit into the MoA CSV
  path (or a small driver `MERGE` of `HAS_MECHANISM`), removing its
  `import sqlite3`-into-our-DB path.

### 4.6 Tests

- Rewrite `test_enrichment.py`, `test_repository.py`,
  `test_build_pharmacovigilance.py`. Add a Neo4j fixture — recommended
  `testcontainers[neo4j]` (dev dep) with a session-scoped ephemeral container +
  per-test cleanup, skipped gracefully when Docker is absent. Fixture replaces
  the old `db.build_db(tmp_path, ...)` seed helpers with small Cypher/CSV seeding.
- `test_sources.py`, `test_normalizer.py` stay as-is (parsing/derivation, no DB).

### 4.7 Dependencies — `pyproject.toml`

- Runtime: add `neo4j` (official driver). Optionally `pydantic-settings`.
- Dev: add `testcontainers[neo4j]` (if using the container fixture).
- Run `uv add neo4j` / `uv add --group dev "testcontainers[neo4j]"` so `uv.lock`
  updates.

### 4.8 Cleanup (no backcompat)

- Delete `src/data/pharmacovigilance/db.py` and stale pre-SQLite JSON leftovers
  (`sider_effects.json`, `__sider_effects.json`).
- Update `.gitignore`: drop the `*.db` pharmacovigilance ignore; add the Neo4j
  import/staging CSV dir + any local Neo4j data dir if staged in-repo.
- Update the pharmacovigilance spec/plan docs under `docs/superpowers/` that
  still describe the SQLite (or JSON) backing.

---

## 5. Verification (end-to-end)

1. **Server up** — local Neo4j running (`bolt://localhost:7687`), creds via env;
   confirm `driver.verify_connectivity()`.
2. **Build** — run the ETL against existing `tmp/` sources to emit CSVs + run
   `neo4j-admin ... import`. Sanity-check counts: `MATCH (d:Drug) RETURN count(d)`;
   `MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r)` ≈ ~45M after dedup (down from
   89M rows).
3. **Read path** — instantiate the new `PharmacovigilanceStore`, call
   `get_enriched_drug(cid, store)` for a known CID (with SIDER effects + a
   TWOSIDES interaction); spot-check `Drug.side_effects` / `.interactions` against
   a throwaway old-SQLite build for the same CID.
4. **Risk engine** — run `RiskEngine.assess(patient)` on a patient whose meds
   resolve to enriched drugs; confirm `adverse_effect` / `drug_drug` evaluators
   still fire.
5. **Tests** — `uv run pytest tests/test_enrichment.py tests/test_repository.py
   tests/test_build_pharmacovigilance.py` green against the fixture; full
   `uv run pytest` green.
6. Verify `neo4j` driver + `neo4j-admin import` syntax against current docs via
   find-docs before implementing (API/flags change across versions).

---

## 6. Suggested execution order

1. §4.1 + §4.2 — new graph layer + config seam
2. §4.3 — runtime reader (read path first → verifiable before the heavy build)
3. §4.6 — tests for the read path
4. §4.4 — ETL rewrite
5. §4.5 — recovery script port
6. §4.7 + §4.8 — deps + cleanup

Land the read path first so the runtime is verifiable before touching the
89M-row build.

---

## 7. Operational note (Ultraplan teleport blocker)

The repo working tree is **~80 GB** (`.git` 13 GB, `tmp/` 50 GB — TWOSIDES.csv +
full ChEMBL 37 SQLite dump, `src/` 13 GB). This is why the cloud "teleport" to
Ultraplan failed ("repo too large"). These large inputs are build-time only and
should live outside the teleported tree (e.g. a data dir excluded from teleport,
or fetched on demand) before any cloud handoff.
