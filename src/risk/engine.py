"""The risk engine: orchestrate the axis evaluators into a RiskAssessment.

The engine runs each injected evaluator, deduplicates the combined alerts,
sorts them worst-first, and aggregates them into a patient ``tier`` (the worst
severity present) and a ``RiskBurden`` (count per severity). It does no I/O: the
patient's drugs must arrive already enriched. A failing evaluator is logged and
skipped so one broken axis never sinks the whole assessment.
"""

from src.data.schemas.patient import Patient
from src.risk.evaluators import (
    AdverseEffectEvaluator,
    AgeModifierEvaluator,
    DrugDiseaseEvaluator,
    DrugDrugEvaluator,
    Evaluator,
)
from src.risk.models import Alert, RiskAssessment, RiskBurden, RiskSeverity
from src.risk.stores import AgeRiskStore, DiseaseInteractionStore
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Ordering of the severity ladder, low to high.
_SEVERITY_ORDER: dict[RiskSeverity, int] = {
    "low": 0,
    "moderate": 1,
    "high": 2,
    "critical": 3,
}


class RiskEngine:
    """Runs a set of evaluators and aggregates their alerts for one patient."""

    def __init__(self, evaluators: list[Evaluator]) -> None:
        self._evaluators = evaluators

    def assess(self, patient: Patient) -> RiskAssessment:
        alerts = self._collect(patient)
        alerts = _dedup(alerts)
        alerts.sort(key=lambda a: _SEVERITY_ORDER[a.severity], reverse=True)
        tier = alerts[0].severity if alerts else "low"
        return RiskAssessment(
            patient_id=patient.id,
            tier=tier,
            burden=_burden(alerts),
            alerts=alerts,
        )

    def _collect(self, patient: Patient) -> list[Alert]:
        alerts: list[Alert] = []
        for evaluator in self._evaluators:
            try:
                alerts.extend(evaluator.evaluate(patient))
            except Exception:  # one broken axis must not sink the assessment
                logger.exception(
                    "Evaluator %s failed; skipping its axis",
                    type(evaluator).__name__,
                )
        return alerts


def _dedup(alerts: list[Alert]) -> list[Alert]:
    """Drop alerts that repeat the same axis, drug set and title."""
    seen: set[tuple[str, tuple[int, ...], str]] = set()
    out: list[Alert] = []
    for alert in alerts:
        key = (alert.axis, tuple(sorted(alert.drug_cids)), alert.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(alert)
    return out


def _burden(alerts: list[Alert]) -> RiskBurden:
    counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
    for alert in alerts:
        counts[alert.severity] += 1
    return RiskBurden(**counts)


def default_engine(
    disease_store: DiseaseInteractionStore, age_store: AgeRiskStore
) -> RiskEngine:
    """Build a RiskEngine with the standard four axis evaluators."""
    return RiskEngine(
        [
            AdverseEffectEvaluator(),
            DrugDrugEvaluator(),
            DrugDiseaseEvaluator(disease_store),
            AgeModifierEvaluator(age_store),
        ]
    )
