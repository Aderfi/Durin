"""Tests de los modelos de dominio en src/data/schemas/drug.py."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.data.schemas import ATCCode, Drug, Interaction, Med, SideEffect


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


def test_interaction_requires_drug_identity():
    with pytest.raises(ValidationError):
        Interaction(mechanism="CYP3A4 inhibition")  # sin fármaco -> inválida


def test_interaction_with_named_drug_ok():
    inter = Interaction(interacting_drug="warfarina", management="vigilar INR")
    assert inter.interacting_drug == "warfarina"


def test_drug_identity_by_cid():
    a = Drug(cid=33613, name="Amoxicillin")
    b = Drug(cid=33613, name="Amoxicillin (otra fuente)")
    c = Drug(cid=2244, name="Aspirin")
    assert a == b
    assert a != c
    assert len({a, b, c}) == 2  # dedup por cid


def test_drug_inchikey_validation_and_skeleton():
    drug = Drug(cid=33613, name="Amoxicillin", inchikey="LSQZJLSUYDQPKJ-NJBDSQKTSA-N")
    assert drug.inchikey_skeleton == "LSQZJLSUYDQPKJ"
    assert drug.has_atc is False
    with pytest.raises(ValidationError):
        Drug(cid=33613, name="Amoxicillin", inchikey="no-es-valido")


def test_drug_has_no_dosage_field():
    assert "dosage" not in Drug.model_fields


def test_med_rejects_duplicate_cids_distinct_objects():
    a = Drug(cid=33613, name="Amoxicillin")
    b = Drug(cid=33613, name="Amoxicillin (dup)")  # mismo cid, otro objeto
    with pytest.raises(ValidationError):
        Med(
            ATC_code=ATCCode(code="J01CA04"),
            name="dup",
            dosage="500 mg",
            frequency="cada 8h",
            start_date=date(2026, 6, 1),
            active_principles=[a, b],
        )
