# The graph-enrichment notebook

`notebooks/4.0-adf-graph-enrichment.ipynb` runs the domain models
(`Drug`, `Med`, `Patient`, `SideEffect`, `Interaction`) against the live Neo4j
graph, through the bridge code that already existed in `src/data/enrichment.py`
and `src/data/repository.py`. Notebook 2.0 exercises the same models with
hand-built values; this one exercises them with whatever is actually in the
graph.

## Why a notebook and not just tests

The test suite already covers `PharmacovigilanceStore` against an ephemeral
Neo4j container (`testcontainers`). This notebook is a different kind of
check: something you run against your own local database, that prints what it
found so you can look at it, rather than asserting pass/fail in CI. It follows
the same shape as notebook 2.0 — same bootstrap cell, same
`%load_ext autoreload`.

## Connection handling

The notebook doesn't assume Neo4j is running or reachable. Cell 3 opens
`PharmacovigilanceStore`, and if that raises `ServiceUnavailable` or
`AuthError`, `STORE` is set to `None` and every later cell checks for that
before doing anything:

```python
if STORE is None or SAMPLE_CID is None:
    print("DB skipped — ...")
```

So the notebook runs start to finish and exits cleanly whether or not you have
a local Neo4j up — it just skips the cells that need one.

## Picking a CID to test with

Cell 3 needs a real `:Drug` CID that has at least one `HAS_SIDE_EFFECT` edge,
so there's something to show. The query:

```cypher
MATCH (d:Drug)-[:HAS_SIDE_EFFECT]->()
WITH DISTINCT d ORDER BY rand() LIMIT 1
RETURN d.cid AS cid, labels(d) AS labels, properties(d) AS props
```

picks one at random each run (`ORDER BY rand()`), rather than a fixed CID —
an earlier version preferred CID 33613 (amoxicillin) deterministically; that
query is still in the notebook, commented out, as the fallback if you want a
repeatable run instead of a random one. Getting the whole node back (not just
the CID) means the cell can print every label and property on it, not only
the one field the rest of the notebook needs.

## What each section checks

- **Rows → models**: `STORE.side_effects(cid)` and `STORE.interactions(cid)`
  are asserted to return actual `SideEffect`/`Interaction` instances — meaning
  whatever came back from Cypher satisfied every validator (provenance
  present, MedDRA code pattern, severity/source literals) without raising.
- **Local enrichment**: builds a bare `Drug(cid=..., name=...)` and calls
  `enrich_drug(base, STORE)`, then prints the counts of effects/interactions
  it picked up — the no-network path, since `enrich_drug` only reads from the
  graph.
- **Full patient**: wraps the enriched `Drug` in a `Med` and a `Patient`, the
  same shape as notebook 2.0, to confirm graph-sourced data composes through
  the whole model hierarchy and still passes every validator on `Med` and
  `Patient`, not just on `Drug`.
- **PubChem + graph (optional, marked ⚠️)**: `get_enriched_drug` additionally
  resolves chemical identity over the network via PubChem. Commented out by
  default — the rest of the notebook doesn't need network access to be
  useful.

The last cell closes the store if one was opened (`STORE.close()`), or says so
if there wasn't one to close.

## Scope

This notebook checks that real graph data satisfies the domain models. It
doesn't check query performance, doesn't check the ETL that built the graph
in the first place (that's `build_pharmacovigilance.py`'s job, covered in the
pipeline and Snakemake docs), and doesn't run in CI — it's a manual, local
sanity check, not an automated test.
