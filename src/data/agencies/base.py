"""Common contract for national medicines-agency adapters."""

from typing import Protocol, runtime_checkable

from pydantic import Field

from src.data.schemas.base import DomainModel
from src.data.schemas.drug import ATCCode
from src.data.schemas.types import NonEmptyStr


class Product(DomainModel):
    """A marketed medicinal product from a national catalog.

    Minimal shape shared by all agencies. `active_principle_names` are resolved
    to PubChem CIDs downstream (identity layer).
    """

    national_code: NonEmptyStr = Field(description="National registry code.")
    name: NonEmptyStr = Field(description="Brand/product name.")
    atc: ATCCode | None = Field(
        default=None, description="ATC classification, if known."
    )
    active_principle_names: list[NonEmptyStr] = Field(
        description="Active principle names; resolved to CIDs downstream."
    )


@runtime_checkable
class AgencyAdapter(Protocol):
    """Interface every national-agency adapter implements."""

    def lookup_product(self, query: str) -> list[Product]:
        """Search the national catalog for products matching ``query``."""
        ...

    def get_active_principles(self, product: Product) -> list[Product]:
        """Return ``product`` with its active principles resolved/expanded."""
        ...
