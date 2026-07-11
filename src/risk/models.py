"""Domain models produced by the risk engine.

An ``Alert`` is a single fired risk rule; a ``RiskAssessment`` aggregates the
alerts for one patient into a worst-case ``tier`` plus a per-severity
``RiskBurden`` count. Every alert carries ``Provenance`` (project invariant):
an alert can never exist without naming the source of the fact behind it.
"""

from typing import Literal

from pydantic import Field

from src.data.schemas import Provenance
from src.data.schemas.base import DomainModel
from src.data.schemas.types import NonEmptyStr

RiskSeverity = Literal["low", "moderate", "high", "critical"]
RiskAxis = Literal["adverse_effect", "drug_drug", "drug_disease", "age_modifier"]


class Alert(DomainModel):
    """A single fired risk rule, traceable to its source fact."""

    axis: RiskAxis = Field(description="Which risk axis produced this alert.")
    severity: RiskSeverity = Field(description="Unified risk severity of the alert.")
    drug_cids: list[int] = Field(
        description="Involved drug CIDs: 1, except 2 for the drug_drug axis."
    )
    disease_icd10: str | None = Field(
        default=None, description="ICD10 code, for the drug_disease axis."
    )
    title: NonEmptyStr = Field(description="Short human-readable summary of the risk.")
    detail: str | None = Field(default=None, description="Optional longer explanation.")
    recommendation: str | None = Field(
        default=None, description="Optional clinical recommendation."
    )
    provenance: Provenance = Field(
        description="Source of the underlying fact (required)."
    )


class RiskBurden(DomainModel):
    """Count of alerts per severity, reflecting cumulative polypharmacy load."""

    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    moderate: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)


class RiskAssessment(DomainModel):
    """The risk engine's output for one patient."""

    patient_id: int = Field(description="Identifier of the assessed patient.")
    tier: RiskSeverity = Field(
        description="Severity of the worst alert ('low' if there are none)."
    )
    burden: RiskBurden = Field(description="Alert counts per severity.")
    alerts: list[Alert] = Field(
        default_factory=list, description="All alerts, sorted worst-first."
    )
