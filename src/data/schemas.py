import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.locales.loader import t


class Patient(BaseModel):
    id: int
    name: str
    age: int
    birth_date: datetime.date  # Format: datetime.strftime("%Y-%m-%d")
    number_of_meds: int
    polymedicated: bool
    diseases: list[str]
    medication: dict[str, int | float]  # Format: {medication_name: dosage}

    ...

class Drug(BaseModel):
    id: int
    name: str
    dosage: tuple[int | float, str] = Field(default_factory=lambda: (0, "mg"))  # Format: (amount, unit)
    chemical_group: str
    pharmacological_class: str
    side_effects: list[str] = Field(default_factory=list)  # List of side effects
    interactions: list[str] = Field(default_factory=list)  # List of interactions

    ...


#@field_validator
@classmethod
def cleanup_dosage(cls, v: tuple[int | float, str]) -> tuple[int | float, str]:
        if not isinstance(v, tuple) or len(v) != 2:
            raise ValueError("Dosage must be a tuple of (number, unit)")
        return v


@model_validator(mode="after")
def check_age_eq_birth_date(self):
    computed = (datetime.date.today() - self.birth_date).days // 365
    if abs(computed - self.age) > 1:
        raise ValueError("age does not match birth_date")
    return self
