"""Tests for the risk store interfaces and placeholders in src/risk/stores.py."""

import pytest
from pydantic import ValidationError

from src.data.schemas import Provenance
from src.risk.stores import (
    AgeRule,
    DiseaseInteraction,
    EmptyDiseaseInteractionStore,
    SeedAgeRiskStore,
)


def test_empty_disease_store_resolves_nothing():
    store = EmptyDiseaseInteractionStore()
    assert store.lookup(2244, "N18") is None


def test_disease_interaction_requires_provenance():
    ok = DiseaseInteraction(
        cid=2244,
        icd10="N18",
        severity="high",
        description="NSAID worsens chronic kidney disease",
        provenance=Provenance(source="openFDA"),
    )
    assert ok.icd10 == "N18"
    with pytest.raises(ValidationError):
        DiseaseInteraction(
            cid=2244, icd10="N18", severity="high", description="x"
        )  # no provenance


def test_seed_age_store_filters_by_cid_and_age_range():
    rule = AgeRule(
        cid=3016,
        min_age=65,
        max_age=None,
        severity="high",
        description="Benzodiazepine potentially inappropriate in the elderly",
        provenance=Provenance(source="BEERS"),
    )
    store = SeedAgeRiskStore(rules=[rule])
    assert store.rules_for(3016, 70) == [rule]
    assert store.rules_for(3016, 40) == []  # below min_age
    assert store.rules_for(9999, 70) == []  # different cid


def test_seed_age_store_has_a_default_beers_seed():
    store = SeedAgeRiskStore()
    # Diphenhydramine (CID 3100): anticholinergic, avoid in the elderly.
    rules = store.rules_for(3100, 80)
    assert rules
    assert all(r.provenance.source == "BEERS" for r in rules)
