"""Tests for the per-axis risk evaluators in src/risk/evaluators/."""

from collections.abc import Iterable
from datetime import date

from src.data.schemas import ATCCode, Drug, Interaction, Med, Provenance, SideEffect
from src.data.schemas.patient import Patient
from src.risk.evaluators import (
    AdverseEffectEvaluator,
    AgeModifierEvaluator,
    DrugDiseaseEvaluator,
    DrugDrugEvaluator,
)
from src.risk.stores import (
    DiseaseInteraction,
    EmptyDiseaseInteractionStore,
    SeedAgeRiskStore,
)


def _prov(source: str = "SIDER") -> Provenance:
    return Provenance(source=source)


def _drug(
    cid: int,
    name: str,
    *,
    side_effects: Iterable[SideEffect] = (),
    interactions: Iterable[Interaction] = (),
) -> Drug:
    return Drug(
        cid=cid,
        name=name,
        side_effects=list(side_effects),
        interactions=list(interactions),
    )


def _patient(
    drugs: list[Drug],
    *,
    diseases: Iterable[str] = (),
    age: int = 80,
    active: bool = True,
) -> Patient:
    start = date(2020, 1, 1) if active else date(1990, 1, 1)
    end = None if active else date(2000, 1, 1)
    med = Med(
        ATC_code=ATCCode(code="A01"),
        name="med",
        dosage="1",
        frequency="qd",
        start_date=start,
        end_date=end,
        active_principles=list(drugs),
    )
    birth = date.today().replace(year=date.today().year - age)
    return Patient(
        id=1,
        name="patient",
        age=age,
        birth_date=birth,
        number_of_meds=max(len(drugs), 1),
        polymedicated=True,
        diseases=list(diseases),
        medication=[med],
    )


# --- adverse effect ---------------------------------------------------------


def test_adverse_effect_emits_only_severe():
    severe = SideEffect(name="GI haemorrhage", severity="severe", provenance=_prov())
    mild = SideEffect(name="nausea", severity="mild", provenance=_prov())
    drug = _drug(2244, "aspirin", side_effects=[severe, mild])
    alerts = AdverseEffectEvaluator().evaluate(_patient([drug]))
    assert len(alerts) == 1
    assert alerts[0].axis == "adverse_effect"
    assert alerts[0].severity == "high"
    assert alerts[0].drug_cids == [2244]
    assert alerts[0].title == "GI haemorrhage"


def test_adverse_effect_skips_inactive_medication():
    severe = SideEffect(name="GI haemorrhage", severity="severe", provenance=_prov())
    drug = _drug(2244, "aspirin", side_effects=[severe])
    assert AdverseEffectEvaluator().evaluate(_patient([drug], active=False)) == []


# --- drug-drug --------------------------------------------------------------


def test_drug_drug_matches_pair_in_patient_once():
    a_i = Interaction(
        interacting_drug="warfarin",
        interacting_cid=5090,
        mechanism="Increased risk of bleeding",
        severity="major",
        provenance=_prov("TWOSIDES"),
    )
    b_i = Interaction(
        interacting_drug="aspirin",
        interacting_cid=2244,
        mechanism="Increased risk of bleeding",
        severity="major",
        provenance=_prov("TWOSIDES"),
    )
    a = _drug(2244, "aspirin", interactions=[a_i])
    b = _drug(5090, "warfarin", interactions=[b_i])
    alerts = DrugDrugEvaluator().evaluate(_patient([a, b]))
    assert len(alerts) == 1  # A-B and B-A dedup to one
    assert alerts[0].severity == "high"  # major -> high
    assert sorted(alerts[0].drug_cids) == [2244, 5090]


def test_drug_drug_ignores_partner_absent_from_patient():
    inter = Interaction(
        interacting_drug="warfarin",
        interacting_cid=5090,
        mechanism="Increased risk of bleeding",
        provenance=_prov("TWOSIDES"),
    )
    a = _drug(2244, "aspirin", interactions=[inter])
    assert DrugDrugEvaluator().evaluate(_patient([a])) == []


# --- drug-disease -----------------------------------------------------------


class _FakeDiseaseStore:
    def __init__(self, hit: DiseaseInteraction) -> None:
        self._hit = hit

    def lookup(self, cid: int, icd10: str) -> DiseaseInteraction | None:
        return self._hit if (cid == 2244 and icd10 == "N18") else None


def test_drug_disease_emits_from_store():
    hit = DiseaseInteraction(
        cid=2244,
        icd10="N18",
        severity="high",
        description="NSAID worsens chronic kidney disease",
        provenance=_prov("openFDA"),
    )
    patient = _patient([_drug(2244, "aspirin")], diseases=["N18"])
    alerts = DrugDiseaseEvaluator(_FakeDiseaseStore(hit)).evaluate(patient)
    assert len(alerts) == 1
    assert alerts[0].axis == "drug_disease"
    assert alerts[0].disease_icd10 == "N18"
    assert alerts[0].severity == "high"


def test_drug_disease_empty_store_yields_nothing():
    patient = _patient([_drug(2244, "aspirin")], diseases=["N18"])
    evaluator = DrugDiseaseEvaluator(EmptyDiseaseInteractionStore())
    assert evaluator.evaluate(patient) == []


# --- age modifier -----------------------------------------------------------


def test_age_modifier_fires_within_range():
    patient = _patient([_drug(3016, "diazepam")], age=80)
    alerts = AgeModifierEvaluator(SeedAgeRiskStore()).evaluate(patient)
    assert len(alerts) == 1
    assert alerts[0].axis == "age_modifier"
    assert alerts[0].drug_cids == [3016]


def test_age_modifier_silent_out_of_range():
    patient = _patient([_drug(3016, "diazepam")], age=40)
    assert AgeModifierEvaluator(SeedAgeRiskStore()).evaluate(patient) == []
