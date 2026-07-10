import json
import re
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.locales.loader import t

_ATC_DATA_PATH = Path(__file__).parent.parent / "atc" / "codes.json"
_ATC_LOOKUP: dict[str, str] = json.loads(_ATC_DATA_PATH.read_text())

_ATC_PATTERN = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")

FrequencyCategory = Literal[
    "very common",   # ≥1/10
    "common",        # ≥1/100 to <1/10
    "uncommon",      # ≥1/1,000 to <1/100
    "rare",          # ≥1/10,000 to <1/1,000
    "very rare",     # <1/10,000
]

SeverityLevel = Literal["mild", "moderate", "severe"]


class ATCCode(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

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

class SideEffect(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    name: str
    description: str
    severity: SeverityLevel
    frequency: FrequencyCategory | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in {"mild", "moderate", "severe"}:
            raise ValueError(t("validation.invalid_severity_level", severity=v))
        return v

class Interaction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    interacting_drug_id: ATCCode | None = None  # Optional ID of the interacting drug
    interacting_drug: str | None = None # Name/Id of the interacting drug
    interaction_type: Literal["PD", "PK"] | None = None
    severity: Literal["minor", "moderate", "major", "contraindicated"] | None = None
    mechanism: str | None = None  # ej. "inhibición de CYP3A4"
    description: str | None = None
    management: str | None = None  # ej. "evitar combinación" / "ajustar dosis"


class Drug(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    id: int
    name: str
    dosage: tuple[int | float, str] = Field(default_factory=lambda: (0, "mg"))
    chemical_group: ATCCode
    side_effects: list[SideEffect] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)

    @field_validator("dosage")
    @classmethod
    def cleanup_dosage(cls, v: tuple[int | float, str]) -> tuple[int | float, str]:
        if not isinstance(v, tuple) or len(v) != 2: # noqa: PLR2004
            raise ValueError(t("validation.invalid_dosage"), dosage=v)
        return v

if __name__ == "__main__":

    from src.locales.loader import load_locale
    print("Claves cargadas:", list(load_locale("es").keys()))

    print(f"Códigos ATC cargados: {len(_ATC_LOOKUP)}")

    sample_code = next(iter(_ATC_LOOKUP))
    print(f"Ejemplo de código en codes.json: {sample_code!r} -> {_ATC_LOOKUP[sample_code]!r}")

    atc = ATCCode(code=sample_code)
    print(f"ATCCode resuelto: code={atc.code}, name={atc.name}, level={atc.level}")

    try:
        fake = ATCCode(code="N03AX24")
        print(f"Código válido en formato pero sin nombre: {fake}")
    except Exception as e:
        print(f"Error esperado con formato inválido: {e}")

    try:
        ATCCode(code="not-a-code")
    except Exception as e:
        print(f"Validación de formato funcionando correctamente: {e}")

    drug = Drug(
        id=1,
        name="Test Drug",
        chemical_group=ATCCode(code=sample_code),
    )
    print(f"Drug creado: {drug.name}, chemical_group.name={drug.chemical_group.name}")
