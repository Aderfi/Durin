# The pharmacovigilance data pipeline

A drug's side effects and interactions come from four sources, combined
offline into the Neo4j graph the risk engine reads at runtime. This covers
where that data comes from, how it's coded, and the one place a language
model is involved.

## Sources

- **SIDER 4.1** — adverse effects extracted from drug labels. Already coded,
  but not to MedDRA the way the original SQLite schema assumed: SIDER's
  fifth column is a UMLS CUI, not a MedDRA code (see the Neo4j migration doc
  for how that got caught and fixed). CC BY-NC-SA 4.0, so it's fine for this
  academic project but wouldn't be for a commercial one. Identifiers are
  STITCH compound IDs; `stitch_to_cid()` converts the flat form
  (`CID1xxxxxxx`) to a PubChem CID by stripping the prefix and leading zeros.
- **ChEMBL** — mechanism of action, joined to PubChem CIDs via UniChem's
  ChEMBL-to-CID crosswalk. CC BY-SA 3.0.
- **TWOSIDES** — drug-drug interaction evidence with a PRR (proportional
  reporting ratio) per pair, natively keyed by PubChem CID, no crosswalk
  needed. CC0.
- **openFDA** — drug label text, fetched on demand (not part of the batch
  ETL) and cached locally. This is the only source that's free text rather
  than an already-coded fact, which is why it's the only one that touches the
  LLM normalizer described below.

SIDER, ChEMBL, and TWOSIDES are parsed and merged by
`scripts/build_pharmacovigilance.py` into the CSVs Neo4j bulk-imports (see the
Snakemake doc for how that's wired up). openFDA fetching
(`fetch_openfda_label` in `src/data/sources.py`) is a separate, on-demand path
with its own `tenacity` retry and a local cache directory, not something the
batch ETL runs unattended.

## Provenance

Every `SideEffect` and `Interaction` requires a `Provenance` — `source`,
`source_id`, `retrieved` — enforced by Pydantic, not by convention. There's no
way to build one without it. `source` is a closed list of six values
(`SIDER`, `ChEMBL`, `TWOSIDES`, `openFDA`, `CIMA`, `LLM_NORMALIZED`), plus
`BEERS` for the risk engine's age-modifier rules. For `LLM_NORMALIZED`
records, `source_id` holds the original free text that got coded, not a
database id — so you can always trace a coded effect back to the exact
sentence it came from.

## Severity: derived where the source doesn't say

None of the four sources gives a clean mild/moderate/severe rating.
`derive_severity()` in `src/data/sources.py` fills that gap with a small rule:
if a MedDRA code is in `_SERIOUS_MEDDRA_CODES` (a seed set — currently one
code, `10017955`, gastrointestinal haemorrhage — the docstring says it's
meant to be extended from the MedDRA "Important Medical Event" list, not that
it already is) or an `is_serious` flag is set, severity is `severe`; a coded
effect with neither signal is `moderate`; no code at all is `None`. The
runtime call site (`enrichment.py`) always passes `is_serious=False`, so in
practice today only the hardcoded code set can trigger `severe` through this
path. When a value is derived rather than read directly, `severity_derived`
is set to `True` — this is a plain rule, not an LLM call, and it never
touches `TermNormalizer`.

## Where the LLM comes in, and where it doesn't

openFDA labels are prose. Turning "can't sleep" into a MedDRA Preferred Term
needs something more than string matching, and that's the one job a local LLM
does in this pipeline — nowhere else.

The approach (`src/data/pharmacovigilance/normalizer.py`) is retrieve-then-rank:

1. **Retrieval** — `SapBERTCandidateGenerator` embeds the free-text phrase and
   every term in the closed MedDRA vocabulary with a biomedical bi-encoder
   (SapBERT), and returns the top-K nearest terms by cosine similarity
   (`DEFAULT_TOP_K = 5`).
2. **Ranking** — `LocalLLMNormalizer` shows the model those candidates and
   asks it to answer with a single number: the index of the best match, or
   `0` for none. The model runs locally via llama.cpp
   (`llama_cpp.Llama`, in-process, no server), against a GGUF checkpoint.
   Output is grammar-constrained (`root ::= [0-9]+`) so it can only ever
   produce digits, and sampling is deterministic (`temperature=0.0`).

The model never sees or produces a MedDRA code — it picks an index, and the
actual (term, code) pair is read back from the candidate list Python already
has in hand. An out-of-range index, a non-numeric answer, or `0` all resolve
to `None`, dropping the phrase rather than risking a wrong code.
`normalize()` in `normalizer.py` is the whole contract: candidates in, an
optional `(term, code)` tuple out.

This only runs inside `scripts/build_pharmacovigilance.py`, during the offline
ETL, when `--openfda-reactions` is passed. The runtime read path
(`enrichment.py`) never imports the normalizer and never calls the model —
by the time a request touches the live graph, any openFDA text has already
been coded once, offline, or dropped.

One implementation note: the original plan had the model answer with the
term name itself, looked up exactly against the candidate list. That was
changed to an index during implementation — a small model reliably producing
a single digit is an easier bet than reproducing a 15-20 character term with
exact casing, and a formatting slip in the latter would wrongly produce
`None` even when the model's pick was right. The actual safety property (a
hallucinated code is impossible, since the code always comes from the local
table) didn't change.

## What this doesn't do

Nothing here does clinical reasoning. The LLM's only job is picking the
closest known term for a phrase a structured source already wrote; it never
decides that a drug causes an effect. openFDA's `split_adverse_reactions` is
a delimiter-based text split, not medical NER — good enough because the
normalizer downstream is precision-first and drops what it can't confidently
match, not because the splitting itself is careful. Bulk unattended fetching
of openFDA data isn't part of the ETL script; it consumes an already-prepared
`reactions.json`, with on-demand fetching living separately in
`fetch_openfda_label`.
