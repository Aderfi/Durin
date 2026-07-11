"""Tests for RiskEngine aggregation in src/risk/engine.py."""

from datetime import date

from src.data.schemas import ATCCode, Drug, Med, Provenance
from src.data.schemas.patient import Patient
from src.risk.engine import RiskEngine, default_engine
from src.risk.models import Alert
from src.risk.stores import EmptyDiseaseInteractionStore, SeedAgeRiskStore


def _patient(medication: list[Med] | None = None, age: int = 80) -> Patient:
    birth = date.today().replace(year=date.today().year - age)
    return Patient(
        id=7,
        name="patient",
        age=age,
        birth_date=birth,
        number_of_meds=1,
        polymedicated=True,
        diseases=[],
        medication=medication or [],
    )


def _alert(axis="adverse_effect", severity="high", cids=(2244,), title="t") -> Alert:
    return Alert(
        axis=axis,
        severity=severity,
        drug_cids=list(cids),
        title=title,
        provenance=Provenance(source="SIDER"),
    )


class _StaticEvaluator:
    def __init__(self, alerts: list[Alert]) -> None:
        self._alerts = alerts

    def evaluate(self, patient: Patient) -> list[Alert]:
        return list(self._alerts)


class _BoomEvaluator:
    def evaluate(self, patient: Patient) -> list[Alert]:
        raise RuntimeError("boom")


def test_assess_no_alerts_is_low_tier():
    assessment = RiskEngine([]).assess(_patient())
    assert assessment.tier == "low"
    assert assessment.alerts == []
    assert assessment.burden.high == 0
    assert assessment.patient_id == 7


def test_assess_sorts_worst_first_and_sets_tier():
    ev = _StaticEvaluator(
        [
            _alert(severity="moderate", title="a"),
            _alert(severity="critical", title="b"),
            _alert(severity="high", title="c"),
        ]
    )
    assessment = RiskEngine([ev]).assess(_patient())
    assert [a.severity for a in assessment.alerts] == ["critical", "high", "moderate"]
    assert assessment.tier == "critical"


def test_assess_counts_burden_per_severity():
    ev = _StaticEvaluator(
        [
            _alert(severity="high", title="a"),
            _alert(severity="high", title="b"),
            _alert(severity="moderate", title="c"),
        ]
    )
    burden = RiskEngine([ev]).assess(_patient()).burden
    assert burden.high == 2
    assert burden.moderate == 1
    assert burden.critical == 0


def test_assess_dedups_identical_alerts_across_evaluators():
    shared = _alert(axis="drug_drug", cids=(2244, 5090), title="bleeding")
    engine = RiskEngine([_StaticEvaluator([shared]), _StaticEvaluator([shared])])
    assert len(engine.assess(_patient()).alerts) == 1


def test_assess_isolates_a_failing_evaluator():
    engine = RiskEngine([_BoomEvaluator(), _StaticEvaluator([_alert()])])
    assessment = engine.assess(_patient())
    assert len(assessment.alerts) == 1  # boom skipped, other still ran


def test_default_engine_runs_offline():
    med = Med(
        ATC_code=ATCCode(code="N05"),
        name="diazepam",
        dosage="5 mg",
        frequency="qd",
        start_date=date(2024, 1, 1),
        active_principles=[Drug(cid=3016, name="diazepam")],
    )
    engine = default_engine(EmptyDiseaseInteractionStore(), SeedAgeRiskStore())
    assessment = engine.assess(_patient(medication=[med], age=80))
    assert any(a.axis == "age_modifier" for a in assessment.alerts)
    assert assessment.tier == "high"
