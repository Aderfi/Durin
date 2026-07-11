"""Tests for the domain models in src/data/schemas/drug.py."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.data.schemas import ATCCode, Drug, Interaction, Med, Provenance, SideEffect


def _prov() -> Provenance:
    return Provenance(source="SIDER", source_id="CID100002244")


def test_atccode_derived_groups():
    atc = ATCCode(code="J01CA04")  # amoxicillin, level 5
    assert atc.is_substance is True
    assert atc.level == 5
    assert atc.anatomical_group == "ANTIINFECTIVES FOR SYSTEMIC USE"  # J
    assert atc.therapeutic_group == "ANTIBACTERIALS FOR SYSTEMIC USE"  # J01
    assert (
        atc.pharmacological_class == "BETA-LACTAM ANTIBACTERIALS, PENICILLINS"
    )  # J01C
    assert atc.chemical_subgroup == "Penicillins with extended spectrum"  # J01CA


def test_atccode_higher_level_has_no_deeper_groups():
    atc = ATCCode(code="J01")  # level 2
    assert atc.is_substance is False
    assert atc.therapeutic_group == "ANTIBACTERIALS FOR SYSTEMIC USE"
    assert atc.chemical_subgroup is None


def test_sideeffect_description_optional():
    se = SideEffect(name="rash", severity="mild", provenance=_prov())
    assert se.description is None


def test_sideeffect_rejects_empty_name():
    with pytest.raises(ValidationError):
        SideEffect(name="", severity="mild", provenance=_prov())


def test_sideeffect_requires_provenance():
    with pytest.raises(ValidationError):
        SideEffect(name="nausea", severity="mild")  # no provenance


def test_sideeffect_severity_optional_and_derived_flag():
    se = SideEffect(name="nausea", provenance=_prov())
    assert se.severity is None
    assert se.severity_derived is False

    se2 = SideEffect(
        name="gi haemorrhage",
        severity="severe",
        severity_derived=True,
        meddra_code="10017955",
        provenance=_prov(),
    )
    assert se2.severity == "severe"
    assert se2.severity_derived is True
    assert se2.meddra_code == "10017955"


def test_sideeffect_rejects_bad_meddra_code():
    with pytest.raises(ValidationError):
        SideEffect(name="nausea", meddra_code="ABC", provenance=_prov())


def test_interaction_requires_drug_identity():
    with pytest.raises(ValidationError):
        Interaction(mechanism="CYP3A4 inhibition", provenance=_prov())  # no drug


def test_interaction_requires_provenance():
    with pytest.raises(ValidationError):
        Interaction(interacting_drug="warfarin")  # no provenance


def test_interaction_with_named_drug_ok():
    inter = Interaction(
        interacting_drug="warfarina", management="vigilar INR", provenance=_prov()
    )
    assert inter.interacting_drug == "warfarina"


def test_drug_identity_by_cid():
    a = Drug(cid=33613, name="Amoxicillin")
    b = Drug(cid=33613, name="Amoxicillin (otra fuente)")
    c = Drug(cid=2244, name="Aspirin")
    assert a == b
    assert a != c
    assert len({a, b, c}) == 2  # dedup by cid


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
    b = Drug(cid=33613, name="Amoxicillin (dup)")  # same cid, different object
    with pytest.raises(ValidationError):
        Med(
            ATC_code=ATCCode(code="J01CA04"),
            name="dup",
            dosage="500 mg",
            frequency="cada 8h",
            start_date=date(2026, 6, 1),
            active_principles=[a, b],
        )
