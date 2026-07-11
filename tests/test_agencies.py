from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.data.agencies.base import Product
from src.data.agencies.cima import CimaAdapter


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


def test_cima_lookup_product_parses_response():
    fake = {
        "resultados": [
            {
                "nregistro": "65900",
                "nombre": "Amoxicilina Normon 500 mg",
                "vtm": {"nombre": "amoxicillin"},
                "atcs": [{"codigo": "J01CA04"}],
            }
        ]
    }
    with patch("src.data.agencies.cima.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake
        mock_get.return_value.raise_for_status.return_value = None
        products = CimaAdapter().lookup_product("amoxicilina")
    assert products[0].national_code == "65900"
    assert products[0].active_principle_names == ["amoxicillin"]
    assert products[0].atc.code == "J01CA04"
