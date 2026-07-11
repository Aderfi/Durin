"""Age-modifier axis: potentially inappropriate medications given patient age."""

from src.data.schemas.patient import Patient
from src.risk.evaluators.base import active_drugs
from src.risk.models import Alert
from src.risk.stores import AgeRiskStore


class AgeModifierEvaluator:
    """Raises an alert per Beers/STOPP-style rule applicable at the patient's age."""

    def __init__(self, store: AgeRiskStore) -> None:
        self._store = store

    def evaluate(self, patient: Patient) -> list[Alert]:
        alerts: list[Alert] = []
        for drug in active_drugs(patient):
            for rule in self._store.rules_for(drug.cid, patient.age):
                alerts.append(
                    Alert(
                        axis="age_modifier",
                        severity=rule.severity,
                        drug_cids=[drug.cid],
                        title=rule.description,
                        provenance=rule.provenance,
                    )
                )
        return alerts
