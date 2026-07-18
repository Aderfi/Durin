# Design — Notebook 4.0: Pydantic models against the Neo4j graph

**Date:** 2026-07-18
**Status:** Approved

## Goal

Exercise the domain pydantic models (`Drug`, `Med`, `Patient`, `SideEffect`,
`Interaction`, `Provenance`, `ATCCode`) against the **live Neo4j
pharmacovigilance graph** introduced by the SQLite→Neo4j migration (commit
`1554c1e`). Notebook 2.0 already tests the models **offline** with hand-built
instances; this notebook proves the same models validate correctly when
assembled from real graph rows through the existing bridge.

## Non-goals

- No changes to `src/` — the bridge (`PharmacovigilanceStore`, `enrich_drug`,
  `get_enriched_drug`) already exists and is the code under test.
- Notebook 2.0 stays offline-only and untouched.
- No bulk ETL / graph loading (owned by `scripts/build_pharmacovigilance.py`).

## Deliverable

`notebooks/4.0-adf-graph-enrichment.ipynb`. Style mirrors 2.0: Spanish markdown
narration, English code / comments / docstrings (repo convention — locale text
only in JSON5).

## Code under test (already in `src/`)

- `src.data.enrichment.PharmacovigilanceStore` — opens the Neo4j driver, reads
  `SIDE_EFFECTS_BY_CID` / `INTERACTIONS_BY_CID` by CID, assembles validated
  `SideEffect` / `Interaction` models with mandatory `Provenance`. Context
  manager (`__enter__`/`__exit__`) and `close()`.
- `src.data.enrichment.enrich_drug(drug, store)` — returns a copy of a `Drug`
  with `side_effects` / `interactions` populated from the graph.
- `src.data.repository.get_enriched_drug(cid, store)` — PubChem chemical
  identity + graph pharmacovigilance (network path, optional).
- `src.config.neo4j_config()` — env-backed `NEO4J_*` coordinates.

## Sections

1. **Setup** — insert repo root on `sys.path`, `%load_ext autoreload`. Import the
   store, `enrich_drug`, `get_enriched_drug`, and the models. Print the resolved
   `neo4j_config()` URI / user / database (never the password).

2. **Connect + discover a sample CID** — open the store inside
   `try/except (ServiceUnavailable, AuthError)`. On failure: print a clear skip
   message and set `STORE = None`; every later DB cell guards `if STORE is None`.
   When connected, query the graph for a `:Drug` cid carrying at least one
   `HAS_SIDE_EFFECT` edge and bind it to `SAMPLE_CID` — prefer `33613`
   (amoxicillin, the drug used across 2.0) when present, else the first found.
   This keeps the notebook runnable regardless of what the local DB contains.

3. **Graph rows → validated models** — call `STORE.side_effects(SAMPLE_CID)` and
   `STORE.interactions(SAMPLE_CID)`. Assert each element is a genuine
   `SideEffect` / `Interaction` instance; print counts and the first few
   `model_dump()`s. Proves graph rows satisfy pydantic validation: required
   `Provenance`, `MedDRACode` pattern, `SeverityLevel` / `SourceName` literals.

4. **Enrich a locally built Drug (no network)** — construct
   `Drug(cid=SAMPLE_CID, name=...)` by hand, run `enrich_drug(drug, STORE)`,
   show populated `side_effects` / `interactions` counts and `has_atc`. Core
   path with no PubChem dependency.

5. **Full Patient from graph** — wrap the enriched `Drug` in a `Med` and a
   `Patient` (same shape as 2.0), `model_dump()`. Proves graph-fed models
   compose up the whole domain hierarchy and still pass every model validator.

6. **Optional — PubChem + graph** ⚠️ — `get_enriched_drug(SAMPLE_CID, STORE)`
   (network). Flagged optional exactly like 2.0's PubChem section.

7. **Teardown** — `STORE.close()` when open.

## Fallback behavior

Graceful skip. Connection failure or an empty graph never raises out of a cell:
the store is set to `None`, `SAMPLE_CID` to `None`, and each DB cell short-
circuits with a printed message. The notebook runs top-to-bottom without error
whether or not a live, populated Neo4j is reachable.

## Success criteria

- Notebook executes top-to-bottom with no uncaught exception in both states:
  (a) Neo4j down / empty → skip messages; (b) Neo4j up + populated → real
  models shown.
- With a populated DB, sections 3–5 display validated `SideEffect`,
  `Interaction`, enriched `Drug`, and a full `Patient` `model_dump()`.
- No edits to `src/`; notebook 2.0 unchanged.
