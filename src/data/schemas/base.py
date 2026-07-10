from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Common base for all domain models.

    Centralizes the shared configuration: strips whitespace from strings and
    revalidates on every attribute assignment.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )
