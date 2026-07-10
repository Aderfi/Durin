import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.data.schemas import __all__
from src.locales.loader import t


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
