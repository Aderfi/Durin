import json
import re
from pathlib import Path

from pydantic import Field, field_validator

from src.data.schemas.base import DomainModel
from src.data.schemas.types import (
    FrequencyCategory,
    InteractionSeverity,
    InteractionType,
    PubChemCID,
    SeverityLevel,
)
from src.locales.loader import t

_ATC_DATA_PATH = Path(__file__).parent.parent / "atc" / "codes.json"
_ATC_LOOKUP: dict[str, str] = json.loads(_ATC_DATA_PATH.read_text())

_ATC_PATTERN = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")


class ATCCode(DomainModel):
    code: str
    name: str | None = Field(default=None, validate_default=True)

    @field_validator("code")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if not _ATC_PATTERN.match(v):
            raise ValueError(t("validation.invalid_atc_code", code=v))
        return v

    @field_validator("name", mode="after")
    @classmethod
    def resolve_name(cls, v, info):
        code = info.data.get("code")
        return _ATC_LOOKUP.get(code, v)

    @property
    def level(self) -> int:
        """Hierarchical level of ATC code"""
        length_map = {1: 1, 3: 2, 4: 3, 5: 4, 7: 5}
        return length_map.get(len(self.code), 0)

    def get_parent_code(self, level: int) -> str | None:
        """Returns the parent ATC code at the specified level, or None if not applicable."""
        level_lengths = {1: 1, 2: 3, 3: 4, 4: 5, 5: 7}
        target_length = level_lengths.get(level)
        if target_length is None or len(self.code) < target_length:
            return None
        return self.code[:target_length]

    @property
    def pharmacological_class(self) -> str | None:
        """Name of the level 3 ATC (pharmacological class) derived from the code."""
        parent_code = self.get_parent_code(level=3)
        return _ATC_LOOKUP.get(parent_code) if parent_code else None


class SideEffect(DomainModel):
    name: str
    description: str
    severity: SeverityLevel
    frequency: FrequencyCategory | None = None


class Interaction(DomainModel):
    interacting_drug_id: ATCCode | None = None  # Optional ID of the interacting drug
    interacting_drug: str | None = None  # Name/Id of the interacting drug
    interaction_type: InteractionType | None = None
    severity: InteractionSeverity | None = None
    mechanism: str | None = None  # e.g. "CYP3A4 inhibition"
    description: str | None = None
    management: str | None = None  # e.g. "avoid combination" / "adjust dose"


class Drug(DomainModel):
    cid: PubChemCID  # PubChem Compound ID — stable chemical identity of the active principle
    name: str
    dosage: tuple[int | float, str] = Field(default_factory=lambda: (0, "mg"))
    # Optional ATC: a compound resolved by CID may have no ATC classification.
    chemical_group: ATCCode | None = None
    # Chemical identity (populated from PubChem by src.data.repository.get_drug_by_cid).
    molecular_formula: str | None = None
    smiles: str | None = None
    inchikey: str | None = None
    side_effects: list[SideEffect] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
