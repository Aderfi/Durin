"""Pharmacological risk engine: assess a Patient's medication risk.

Public surface: build an engine with ``default_engine`` (injecting the
drug-disease and age stores) and call ``RiskEngine.assess(patient)`` on a
patient whose drugs are already enriched.
"""

from src.risk.engine import RiskEngine, default_engine
from src.risk.models import Alert, RiskAssessment, RiskAxis, RiskBurden, RiskSeverity
from src.risk.stores import (
    AgeRiskStore,
    DiseaseInteractionStore,
    EmptyDiseaseInteractionStore,
    SeedAgeRiskStore,
)

__all__ = [
    "RiskEngine",
    "default_engine",
    "Alert",
    "RiskAssessment",
    "RiskBurden",
    "RiskAxis",
    "RiskSeverity",
    "AgeRiskStore",
    "DiseaseInteractionStore",
    "EmptyDiseaseInteractionStore",
    "SeedAgeRiskStore",
]
