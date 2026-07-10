from datetime import date

from pydantic import Field, model_validator

from src.data.schemas.base import DomainModel
from src.data.schemas.drug import ATCCode, Drug
from src.locales.loader import t


class Med(DomainModel):
    ATC_code: ATCCode
    name: str
    dosage: str

    # Active principles (1 per compound). Populated by an external service
    # (src.data.repository.build_med) resolving each PubChem CID.
    active_principles: list[Drug] = Field(default_factory=list)
    frequency: str

    start_date: date
    end_date: date | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        return self

    @model_validator(mode="after")
    def unique_active_principles(self):
        if len(set(self.active_principles)) != len(self.active_principles):
            raise ValueError(t("validation.duplicate_active_principle"))
        return self

    @property
    def is_active(self) -> bool:
        """Check if the medication is currently active based on the end_date."""
        return self.end_date is None or self.end_date >= date.today()

    @property
    def duration(self) -> int | None:
        """Calculate the duration of the medication in days, or None if end_date is not set."""
        if self.end_date is None:
            return None
        return (self.end_date - self.start_date).days
