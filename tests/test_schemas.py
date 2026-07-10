"""Tests de los modelos de dominio en src/data/schemas/drug.py."""

import pytest
from pydantic import ValidationError

from src.data.schemas.drug import ATCCode, SideEffect


def test_atccode_derived_groups():
    atc = ATCCode(code="J01CA04")  # amoxicilina, nivel 5
    assert atc.is_substance is True
    assert atc.level == 5
    assert atc.anatomical_group == "ANTIINFECTIVES FOR SYSTEMIC USE"  # J
    assert atc.therapeutic_group == "ANTIBACTERIALS FOR SYSTEMIC USE"  # J01
    assert (
        atc.pharmacological_class == "BETA-LACTAM ANTIBACTERIALS, PENICILLINS"
    )  # J01C
    assert atc.chemical_subgroup == "Penicillins with extended spectrum"  # J01CA


def test_atccode_higher_level_has_no_deeper_groups():
    atc = ATCCode(code="J01")  # nivel 2
    assert atc.is_substance is False
    assert atc.therapeutic_group == "ANTIBACTERIALS FOR SYSTEMIC USE"
    assert atc.chemical_subgroup is None


def test_sideeffect_description_optional():
    se = SideEffect(name="rash", severity="mild")
    assert se.description is None


def test_sideeffect_rejects_empty_name():
    with pytest.raises(ValidationError):
        SideEffect(name="", severity="mild")
