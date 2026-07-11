import pytest
from pydantic import ValidationError

from src.data.agencies.base import Product


def test_product_valid():
    p = Product(
        national_code="65900",
        name="Amoxicilina Normon 500 mg",
        active_principle_names=["amoxicillin"],
    )
    assert p.national_code == "65900"
    assert p.active_principle_names == ["amoxicillin"]


def test_product_rejects_empty_name():
    with pytest.raises(ValidationError):
        Product(national_code="1", name="", active_principle_names=["x"])
