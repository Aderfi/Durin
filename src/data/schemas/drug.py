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

# Longitud del código ATC por nivel jerárquico (1 char L1 ... 7 chars L5).
_LEVEL_LENGTHS = {1: 1, 2: 3, 3: 4, 4: 5, 5: 7}


class ATCCode(DomainModel):
    code: str = Field(description="Código ATC (niveles 1-5), p.ej. 'J01CA04'.")
    name: str | None = Field(
        default=None,
        validate_default=True,
        description="Nombre oficial del código, resuelto desde el catálogo local.",
    )

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
        """Nivel jerárquico del código ATC (0 si la longitud no es válida)."""
        length_to_level = {length: level for level, length in _LEVEL_LENGTHS.items()}
        return length_to_level.get(len(self.code), 0)

    def get_parent_code(self, level: int) -> str | None:
        """Código ancestro al nivel dado, o None si no aplica."""
        target_length = _LEVEL_LENGTHS.get(level)
        if target_length is None or len(self.code) < target_length:
            return None
        return self.code[:target_length]

    def _group_name(self, level: int) -> str | None:
        parent = self.get_parent_code(level)
        return _ATC_LOOKUP.get(parent) if parent else None

    @property
    def anatomical_group(self) -> str | None:
        """Nombre del grupo anatómico principal (nivel 1)."""
        return self._group_name(1)

    @property
    def therapeutic_group(self) -> str | None:
        """Nombre del subgrupo terapéutico (nivel 2)."""
        return self._group_name(2)

    @property
    def pharmacological_class(self) -> str | None:
        """Nombre del subgrupo farmacológico (nivel 3)."""
        return self._group_name(3)

    @property
    def chemical_subgroup(self) -> str | None:
        """Nombre del subgrupo químico (nivel 4)."""
        return self._group_name(4)

    @property
    def is_substance(self) -> bool:
        """True si el código identifica una sustancia (nivel 5)."""
        return self.level == 5


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
