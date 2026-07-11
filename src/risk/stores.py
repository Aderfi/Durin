"""Store interfaces (and placeholders) for the data-fed risk axes.

The drug-disease and age-modifier axes need reference data the project does not
fully own yet. This module defines their read interfaces so the engine is
complete today, and ships a do-nothing drug-disease store plus a minimal Beers
age seed. The full catalogs are later ETL sub-projects; the evaluators are
written against these interfaces, not against concrete datasets.
"""

from typing import Protocol, runtime_checkable

from pydantic import Field

from src.data.schemas import Provenance
from src.data.schemas.base import DomainModel
from src.data.schemas.types import NonEmptyStr
from src.risk.models import RiskSeverity


class DiseaseInteraction(DomainModel):
    """A contraindication/precaution of a drug given a patient condition."""

    cid: int = Field(description="PubChem CID of the drug.")
    icd10: str = Field(description="ICD10 code of the patient condition.")
    severity: RiskSeverity = Field(description="Risk severity of the combination.")
    description: NonEmptyStr = Field(description="Why the combination is risky.")
    provenance: Provenance = Field(description="Source of this fact (required).")


@runtime_checkable
class DiseaseInteractionStore(Protocol):
    """Resolves a (drug, condition) pair to a contraindication, or None."""

    def lookup(self, cid: int, icd10: str) -> DiseaseInteraction | None: ...


class EmptyDiseaseInteractionStore:
    """Placeholder until the drug-disease ETL exists; resolves nothing."""

    def lookup(self, cid: int, icd10: str) -> DiseaseInteraction | None:
        return None


class AgeRule(DomainModel):
    """A potentially-inappropriate-medication rule for an age range."""

    cid: int = Field(description="PubChem CID of the drug.")
    min_age: int | None = Field(
        default=None, description="Lower bound (inclusive); None = no lower bound."
    )
    max_age: int | None = Field(
        default=None, description="Upper bound (inclusive); None = no upper bound."
    )
    severity: RiskSeverity = Field(description="Risk severity when the rule applies.")
    description: NonEmptyStr = Field(description="Why the drug is inappropriate.")
    provenance: Provenance = Field(description="Source of this rule (required).")

    def applies_to(self, age: int) -> bool:
        """True if ``age`` falls within this rule's (inclusive) bounds."""
        if self.min_age is not None and age < self.min_age:
            return False
        if self.max_age is not None and age > self.max_age:
            return False
        return True


@runtime_checkable
class AgeRiskStore(Protocol):
    """Returns the age-modifier rules that apply to a drug at a given age."""

    def rules_for(self, cid: int, age: int) -> list[AgeRule]: ...


def _default_beers_seed() -> list[AgeRule]:
    """A minimal AGS Beers seed; the full catalog is a later ETL phase."""
    prov = Provenance(source="BEERS", source_id="2023 AGS Beers Criteria")
    return [
        AgeRule(
            cid=3100,  # Diphenhydramine: first-gen antihistamine, anticholinergic.
            min_age=65,
            severity="high",
            description="Anticholinergic; avoid in older adults (Beers).",
            provenance=prov,
        ),
        AgeRule(
            cid=3016,  # Diazepam: long-acting benzodiazepine.
            min_age=65,
            severity="high",
            description="Benzodiazepine; increases fall/cognitive risk (Beers).",
            provenance=prov,
        ),
    ]


class SeedAgeRiskStore:
    """In-memory ``AgeRiskStore`` backed by a fixed rule list.

    Defaults to a minimal Beers seed; pass ``rules`` to inject a custom set.
    """

    def __init__(self, rules: list[AgeRule] | None = None) -> None:
        self._rules = rules if rules is not None else _default_beers_seed()

    def rules_for(self, cid: int, age: int) -> list[AgeRule]:
        return [r for r in self._rules if r.cid == cid and r.applies_to(age)]
