import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

type PositiveInt = Annotated[int, Field(gt=0)]
type NonEmptyStr = Annotated[str, Field(min_length=1)]
type date = Annotated[datetime.date, Field(pattern=r"\d{4}-\d{2}-\d{2}")]

@dataclass
class Config:
    str_strip_whitespace = True
    validate_assignment = True

class Patient(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    id: PositiveInt
    name: NonEmptyStr
    age: PositiveInt
    birth_date: date
    number_of_meds: PositiveInt
    polymedicated: bool
    diseases: list[str]
    medication: dict[str, int | float]

    @model_validator(mode="after")
    def check_age_matches_birth_date(self):
        computed = (datetime.date.today() - self.birth_date).days // 365
        if abs(computed - self.age) > 1:
            raise ValueError("age does not match birth_date")
        return self