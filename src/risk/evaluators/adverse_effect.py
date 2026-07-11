"""Adverse-effect axis: severe side effects of the patient's active drugs."""

from src.data.schemas.patient import Patient
from src.risk.evaluators.base import active_drugs
from src.risk.models import Alert
from src.risk.severity import adverse_effect_severity


class AdverseEffectEvaluator:
    """Raises one alert per severe side effect of each active drug."""

    def evaluate(self, patient: Patient) -> list[Alert]:
        alerts: list[Alert] = []
        for drug in active_drugs(patient):
            for effect in drug.side_effects:
                severity = adverse_effect_severity(effect.severity)
                if severity is None:  # only severe effects raise an alert
                    continue
                alerts.append(
                    Alert(
                        axis="adverse_effect",
                        severity=severity,
                        drug_cids=[drug.cid],
                        title=effect.name,
                        provenance=effect.provenance,
                    )
                )
        return alerts
