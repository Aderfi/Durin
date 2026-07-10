import json
import re
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from src.data.schemas.base import DomainModel
from src.data.schemas.types import (
    FrequencyCategory,
    InteractionSeverity,
    InteractionType,
    NonEmptyStr,
    PubChemCID,
    SeverityLevel,
)
from src.locales.loader import t

_ATC_DATA_PATH = Path(__file__).parent.parent / "atc" / "codes.json"
_ATC_LOOKUP: dict[str, str] = json.loads(_ATC_DATA_PATH.read_text())

_ATC_PATTERN = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")

# InChIKey: 14 (esqueleto) - 10 (capas) - 1 (protonación), todo mayúsculas.
_INCHIKEY_PATTERN = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

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
    name: NonEmptyStr = Field(description="Nombre del efecto adverso.")
    description: str | None = Field(
        default=None, description="Descripción clínica opcional."
    )
    severity: SeverityLevel = Field(description="Gravedad: mild | moderate | severe.")
    frequency: FrequencyCategory | None = Field(
        default=None, description="Frecuencia poblacional del efecto, si se conoce."
    )


class Interaction(DomainModel):
    interacting_drug_id: ATCCode | None = Field(
        default=None, description="Código ATC del fármaco que interacciona."
    )
    interacting_drug: NonEmptyStr | None = Field(
        default=None, description="Nombre del fármaco que interacciona."
    )
    interaction_type: InteractionType | None = Field(
        default=None, description="Tipo: PD (farmacodinámica) | PK (farmacocinética)."
    )
    severity: InteractionSeverity | None = Field(
        default=None,
        description="Gravedad: minor | moderate | major | contraindicated.",
    )
    mechanism: str | None = Field(
        default=None, description="Mecanismo, p.ej. 'inhibición CYP3A4'."
    )
    description: str | None = Field(default=None, description="Descripción libre.")
    management: str | None = Field(
        default=None, description="Manejo, p.ej. 'evitar combinación'."
    )

    @model_validator(mode="after")
    def require_drug_identity(self):
        if self.interacting_drug is None and self.interacting_drug_id is None:
            raise ValueError(t("validation.interaction_missing_drug"))
        return self


class Drug(DomainModel):
    cid: PubChemCID = Field(
        description="PubChem Compound ID; identidad química del principio activo."
    )
    name: NonEmptyStr = Field(description="Nombre del compuesto.")
    chemical_group: ATCCode | None = Field(
        default=None,
        description="Clasificación ATC, si se conoce (no viene de PubChem).",
    )
    molecular_formula: NonEmptyStr | None = Field(
        default=None, description="Fórmula molecular."
    )
    smiles: NonEmptyStr | None = Field(default=None, description="Notación SMILES.")
    inchikey: str | None = Field(
        default=None, description="InChIKey (hash estructural estándar)."
    )
    side_effects: list[SideEffect] = Field(
        default_factory=list, description="Efectos adversos conocidos."
    )
    interactions: list[Interaction] = Field(
        default_factory=list, description="Interacciones conocidas."
    )

    @field_validator("inchikey")
    @classmethod
    def validate_inchikey(cls, v: str | None) -> str | None:
        if v is not None and not _INCHIKEY_PATTERN.match(v):
            raise ValueError(t("validation.invalid_inchikey", value=v))
        return v

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Drug) and self.cid == other.cid

    def __hash__(self) -> int:
        return hash(self.cid)

    @property
    def inchikey_skeleton(self) -> str | None:
        """Bloque de conectividad (14 primeros chars del InChIKey); agrupa estereoisómeros/sales."""
        return self.inchikey[:14] if self.inchikey else None

    @property
    def has_atc(self) -> bool:
        """True si el fármaco tiene clasificación ATC asignada."""
        return self.chemical_group is not None
