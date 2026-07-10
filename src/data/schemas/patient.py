import datetime

from pydantic import model_validator

from src.data.schemas.base import DomainModel
from src.data.schemas.medication import Med
from src.data.schemas.types import NonEmptyStr, PositiveInt
from src.locales.loader import t

_AGE_MARGIN_YEARS = 2  # tolerated gap between declared and computed age


class Patient(DomainModel):
    id: PositiveInt
    name: NonEmptyStr
    age: PositiveInt
    birth_date: datetime.date
    number_of_meds: PositiveInt
    polymedicated: bool
    diseases: list[str]
    medication: list[Med]

    @model_validator(mode="after")
    def check_age_matches_birth_date(self):
        computed = (datetime.date.today() - self.birth_date).days // 365
        if abs(computed - self.age) >= _AGE_MARGIN_YEARS:
            raise ValueError(t("validation.age_mismatch"))
        return self
