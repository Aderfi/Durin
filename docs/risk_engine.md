# The risk engine

`src/risk/` turns an enriched `Patient` (drugs already carrying their side
effects and interactions) into a `RiskAssessment`. It does no I/O and no
enrichment itself — by the time a `Patient` reaches `RiskEngine.assess()`,
every `Drug` already has its `side_effects`/`interactions` populated by
`enrichment.py`.

## Four evaluators, one contract

`Evaluator` is a one-method protocol: `evaluate(patient) -> list[Alert]`.
There are four implementations, one per risk axis, each in its own file under
`src/risk/evaluators/`:

- **`AdverseEffectEvaluator`** — one alert per *severe* side effect of an
  active drug. `adverse_effect_severity()` only maps `severe` to a `RiskSeverity`
  of `high`; `mild`, `moderate`, and unset severities return `None` and never
  produce an alert. That's a deliberate filter, not an oversight — the comment
  in `severity.py` says it's to avoid flooding the clinician with trivial
  effects.
- **`DrugDrugEvaluator`** — one alert per interacting pair where *both* drugs
  are present in the patient's active medication, matched by `interacting_cid`
  (chemical identity), not by name. A `seen` set keyed on
  `(frozenset({cid_a, cid_b}), mechanism)` collapses the A→B and B→A
  directions into a single alert.
- **`DrugDiseaseEvaluator`** — looks up each (drug CID, ICD-10 code) pair
  against a `DiseaseInteractionStore`. Today that store is
  `EmptyDiseaseInteractionStore`, which returns `None` unconditionally — this
  axis is wired up but produces no alerts yet, because the drug-disease
  dataset doesn't exist. The evaluator is written against the `Protocol`, not
  against a concrete dataset, so plugging in a real store later doesn't
  require touching this file.
- **`AgeModifierEvaluator`** — checks each active drug against
  `AgeRiskStore.rules_for(cid, age)`. The shipped store,
  `SeedAgeRiskStore`, defaults to two hardcoded Beers-criteria entries
  (diphenhydramine and diazepam, both flagged `high` for age ≥ 65) — a seed,
  not the full AGS Beers list.

`active_drugs(patient)` (in `evaluators/base.py`) is the one piece of shared
logic: it flattens every active principle across the patient's currently
active medications, so each evaluator doesn't reimplement that filter.

## Severity: two source scales, one target scale

`RiskSeverity` (`low` / `moderate` / `high` / `critical`) is the engine's
unified scale. Two of the four axes need mapping into it:

- `interaction_severity()` maps `InteractionSeverity` (`minor` / `moderate` /
  `major` / `contraindicated`) directly across. TWOSIDES interactions ship
  with severity unset, and the code defaults those to `moderate` rather than
  dropping them or guessing `low`/`high` — an explicit choice recorded in a
  comment in `severity.py`, not a silent default.
- `adverse_effect_severity()` maps as described above (severe → high, else
  `None`).

`DrugDiseaseEvaluator` and `AgeModifierEvaluator` don't need a mapping — their
stores already return a `RiskSeverity` directly.

## Aggregation

`RiskEngine.assess()` runs every evaluator, catching and logging exceptions
per evaluator (`logger.exception`) so one broken axis doesn't take down the
whole assessment — it just contributes no alerts for that run. Alerts are
deduplicated on `(axis, sorted(drug_cids), title)`, sorted worst-first, and
folded into a `RiskAssessment`: a `tier` (the single worst severity present,
or `low` if there are no alerts) and a `RiskBurden` (a count per severity —
the polypharmacy load, not just the worst case).

Every `Alert` carries its own `provenance`, copied from the `SideEffect` or
`Interaction` that triggered it — the same "no fact without a source"
invariant from the domain layer applies here too.

## What this doesn't do yet

Two of the four axes (drug-disease, age-modifier) run against placeholder or
seed data, not a real dataset — they exist so the engine's shape is complete,
not because the underlying catalogs have been built. There's no FastAPI
endpoint exposing any of this; `RiskEngine` is a library, called from tests
and notebooks so far. And severity mapping is table-based, not learned or
weighted — there's no scoring model here, just literal-to-literal lookups.
