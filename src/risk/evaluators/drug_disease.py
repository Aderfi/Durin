"""Drug-disease axis: contraindications of active drugs vs patient conditions."""

from src.data.schemas.patient import Patient
from src.risk.evaluators.base import active_drugs
from src.risk.models import Alert
from src.risk.stores import DiseaseInteractionStore


class DrugDiseaseEvaluator:
    """Raises an alert per (drug, condition) contraindication found in the store.

    The store is injected; with ``EmptyDiseaseInteractionStore`` no alert fires
    (the drug-disease dataset is a later ETL phase).
    """

    def __init__(self, store: DiseaseInteractionStore) -> None:
        self._store = store

    def evaluate(self, patient: Patient) -> list[Alert]:
        alerts: list[Alert] = []
        for drug in active_drugs(patient):
            for icd10 in patient.diseases:
                hit = self._store.lookup(drug.cid, icd10)
                if hit is None:
                    continue
                alerts.append(
                    Alert(
                        axis="drug_disease",
                        severity=hit.severity,
                        drug_cids=[drug.cid],
                        disease_icd10=icd10,
                        title=hit.description,
                        provenance=hit.provenance,
                    )
                )
        return alerts
