# Domain modeling: drugs, medications, patients

The domain layer (`src/data/schemas/`) is a set of Pydantic models that validate
data about drugs, medications, and patients. It doesn't fetch data or talk to a
database — that's handled elsewhere (`repository.py`, `enrichment.py`). The
models only check that the data they're given is well-formed.

## ATCCode

An ATC code (the WHO's anatomical/therapeutic/chemical classification) is
validated against a regex and its name is looked up from a local JSON catalog
(`src/data/atc/atc_codes.json`) at construction time. Code length tells you the
hierarchy level (1 char = anatomical group, up to 7 chars = the specific
substance), so `ATCCode` derives `anatomical_group`, `therapeutic_group`,
`pharmacological_class`, and `chemical_subgroup` as properties, each one just a
truncation of the code plus a catalog lookup at that shorter length. There's
also `is_substance` (true at the full 7-character level) and
`get_parent_code(level)` to walk back up the hierarchy.

## Drug

`Drug` is identified by `cid` — a PubChem Compound ID — and that's also its
notion of equality: `__eq__`/`__hash__` compare on `cid` alone, so two `Drug`
instances with the same CID but different field values are still "the same
drug" as far as a `set` or `dict` key is concerned. That's what
`Med.unique_active_principles` relies on to reject a medication listing the
same compound twice.

`inchikey` is validated against the standard InChIKey shape
(`XXXXXXXXXXXXXX-XXXXXXXXXX-X`), and `inchikey_skeleton` exposes just the
first block — the part that's shared across stereoisomers of the same
compound, which is what the SIDER ETL groups by (see the pharmacovigilance
pipeline doc).

`side_effects` and `interactions` default to empty lists. Nothing in `Drug`
itself populates them — that's `enrichment.py`'s job, reading from the Neo4j
graph.

## Provenance, SideEffect, Interaction

`Provenance` (`source`, `source_id`, `retrieved`) is a required field on both
`SideEffect` and `Interaction`. There's no way to construct either without
naming where the fact came from — Pydantic raises a `ValidationError` if you
try. `source` is a closed `Literal` (`SourceName` in `types.py`): `SIDER`,
`ChEMBL`, `TWOSIDES`, `openFDA`, `CIMA`, `LLM_NORMALIZED`, or `BEERS`.

`SideEffect.severity` is optional, because none of the source datasets
reliably supplies one. When the ETL derives a severity instead of reading it
directly (see `derive_severity` in `src/data/sources.py`), it sets
`severity_derived=True` so a consumer can tell "the source said this" from "we
inferred this."

`Interaction` requires either `interacting_drug` (a name) or
`interacting_drug_id` (an ATC code) — a model validator
(`require_drug_identity`) rejects an interaction that names no drug at all.
There's also `interacting_cid`, a PubChem CID used for exact chemical-identity
matching; the risk engine's drug-drug evaluator only fires on CID matches, not
name matches, to avoid false positives from name variants.

## Med and Patient

`Med` wraps one or more `Drug` active principles with dosing information
(`dosage`, `frequency`, `start_date`, `end_date`). `is_active` checks whether
today falls within the date range; `duration` is just the day difference. A
model validator (`check_dates`) rejects a `start_date` after `end_date`.

`Patient` cross-checks `age` against `birth_date`: if the two disagree by two
years or more, construction fails. This exists because both fields are
supplied independently (one is often typed by hand, the other computed), and a
mismatch usually means a data-entry mistake rather than a valid edge case.

## What isn't here

There's no route-of-administration field, no unit-of-measurement enum, no
dosage-form modeling. These were considered and left out — `dosage` is a free
string, not a structured quantity+unit pair. Adding that kind of structure
would mean deciding on a unit vocabulary and a parsing strategy for existing
free-text dosages, and nothing downstream currently needs it, so it wasn't
built.

`DomainModel` (the shared base class in `base.py`) sets
`str_strip_whitespace=True` and `validate_assignment=True` project-wide, so
every model re-validates on attribute mutation, not just at construction.
