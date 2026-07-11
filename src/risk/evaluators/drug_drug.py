"""Drug-drug axis: interactions between active drugs present in the patient."""

from src.data.schemas.patient import Patient
from src.risk.evaluators.base import active_drugs
from src.risk.models import Alert
from src.risk.severity import interaction_severity


class DrugDrugEvaluator:
    """Raises an alert per interacting pair both present in the patient.

    Matches by ``interacting_cid`` (chemical identity), so a name-only
    interaction never fires. Each unordered pair + mechanism is emitted once
    (the A->B and B->A directions collapse to a single alert).
    """

    def evaluate(self, patient: Patient) -> list[Alert]:
        drugs = active_drugs(patient)
        present_cids = {drug.cid for drug in drugs}
        alerts: list[Alert] = []
        seen: set[tuple[frozenset[int], str | None]] = set()
        for drug in drugs:
            for inter in drug.interactions:
                other = inter.interacting_cid
                if other is None or other == drug.cid or other not in present_cids:
                    continue
                key = (frozenset({drug.cid, other}), inter.mechanism)
                if key in seen:
                    continue
                seen.add(key)
                alerts.append(
                    Alert(
                        axis="drug_drug",
                        severity=interaction_severity(inter.severity),
                        drug_cids=sorted({drug.cid, other}),
                        title=inter.mechanism or "Drug-drug interaction",
                        provenance=inter.provenance,
                    )
                )
        return alerts
