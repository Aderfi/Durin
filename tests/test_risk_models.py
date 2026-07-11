"""Tests for the risk-engine domain models in src/risk/models.py."""

import pytest
from pydantic import ValidationError

from src.data.schemas import Provenance
from src.risk.models import Alert, RiskAssessment, RiskBurden


def _prov() -> Provenance:
    return Provenance(source="SIDER", source_id="CID100002244")


def test_alert_requires_provenance():
    with pytest.raises(ValidationError):
        Alert(axis="adverse_effect", severity="high", drug_cids=[2244], title="rash")


def test_alert_constructs_with_fields():
    alert = Alert(
        axis="drug_drug",
        severity="high",
        drug_cids=[2244, 5090],
        title="Increased risk of bleeding",
        provenance=_prov(),
    )
    assert alert.axis == "drug_drug"
    assert alert.drug_cids == [2244, 5090]
    assert alert.disease_icd10 is None


def test_riskburden_defaults_zero():
    burden = RiskBurden()
    assert (burden.critical, burden.high, burden.moderate, burden.low) == (0, 0, 0, 0)


def test_riskassessment_constructs():
    alert = Alert(
        axis="adverse_effect",
        severity="high",
        drug_cids=[2244],
        title="Gastrointestinal haemorrhage",
        provenance=_prov(),
    )
    assessment = RiskAssessment(
        patient_id=1,
        tier="high",
        burden=RiskBurden(high=1),
        alerts=[alert],
    )
    assert assessment.tier == "high"
    assert assessment.burden.high == 1
    assert len(assessment.alerts) == 1
