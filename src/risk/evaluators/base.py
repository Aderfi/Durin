"""The evaluator contract and a shared helper for the risk axes.

An ``Evaluator`` turns a ``Patient`` into a list of ``Alert``s for one risk
axis. Evaluators are pure with respect to the patient (no mutable state) and do
no I/O: the patient's drugs must arrive already enriched (side effects and
interactions populated by ``repository.get_enriched_drug``).
"""

from typing import Protocol, runtime_checkable

from src.data.schemas.drug import Drug
from src.data.schemas.patient import Patient
from src.risk.models import Alert


@runtime_checkable
class Evaluator(Protocol):
    """Produces the alerts of a single risk axis for a patient."""

    def evaluate(self, patient: Patient) -> list[Alert]: ...


def active_drugs(patient: Patient) -> list[Drug]:
    """Every active principle across the patient's currently-active medications."""
    return [
        drug
        for med in patient.medication
        if med.is_active
        for drug in med.active_principles
    ]
