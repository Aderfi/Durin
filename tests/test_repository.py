import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pytest

from src.data import pubchem, repository
from src.data.enrichment import PharmacovigilanceStore
from src.data.pharmacovigilance import db
from src.data.repository import get_enriched_drug
from src.data.schemas import Drug, Med

_AMOX_PROPS = {
    "CID": 33613,
    "Title": "Amoxicillin",
    "MolecularFormula": "C16H19N3O5S",
    "SMILES": "CC1([C@@H](N2...)C",
    "InChIKey": "LSQZJLSUYDQPKJ-NJBDSQKTSA-N",
}


@pytest.fixture
def fake_pubchem(monkeypatch):
    """Replace the network calls with an in-memory lookup."""
    table = {33613: _AMOX_PROPS}
    monkeypatch.setattr(repository, "fetch_compound", lambda cid: table.get(cid))
    monkeypatch.setattr(
        repository,
        "fetch_compounds",
        lambda cids: {c: table[c] for c in cids if c in table},
    )
    return table


def test_get_drug_by_cid_builds_drug(fake_pubchem):
    drug = repository.get_drug_by_cid(33613)
    assert isinstance(drug, Drug)
    assert drug.cid == 33613
    assert drug.name == "Amoxicillin"
    assert drug.molecular_formula == "C16H19N3O5S"
    assert drug.inchikey == "LSQZJLSUYDQPKJ-NJBDSQKTSA-N"
    assert drug.chemical_group is None  # ATC no viene de PubChem


def test_get_drug_by_cid_unknown_returns_none(fake_pubchem):
    assert repository.get_drug_by_cid(99999999) is None


def test_get_drug_by_cid_bare_record_returns_none(monkeypatch):
    # PubChem answers 200 with only {"CID": n} for nonexistent CIDs.
    monkeypatch.setattr(repository, "fetch_compound", lambda cid: {"CID": cid})
    assert repository.get_drug_by_cid(999999999) is None


def test_resolve_active_principles_filters_unresolved(fake_pubchem):
    resolved = repository.resolve_active_principles([33613, 99999999])
    assert [d.cid for d in resolved] == [33613]


def test_build_med_resolves_cids(fake_pubchem):
    med = repository.build_med(
        atc_code="J01CR02",
        name="Augmentin",
        dosage="875/125mg",
        frequency="bid",
        start_date=dt.date(2024, 1, 1),
        cids=[33613],
    )
    assert isinstance(med, Med)
    assert [d.name for d in med.active_principles] == ["Amoxicillin"]


def test_med_rejects_duplicate_active_principles():
    amox = Drug(cid=33613, name="Amoxicillin")
    with pytest.raises(ValueError):
        Med(
            ATC_code={"code": "J01CR02"},
            name="dup",
            dosage="1mg",
            frequency="daily",
            start_date=dt.date(2024, 1, 1),
            active_principles=[amox, amox],
        )


def test_fetch_compound_404_returns_none(monkeypatch):
    class _Resp:
        status_code = 404

    monkeypatch.setattr(pubchem.requests, "get", lambda *a, **k: _Resp())
    pubchem.fetch_compound.cache_clear()
    assert pubchem.fetch_compound(1) is None


def _empty_store(tmp_path: Path, effects: dict | None = None) -> PharmacovigilanceStore:
    db_path = db.build_db(tmp_path / "pv.db", sider=effects)
    return PharmacovigilanceStore(db_path)


def test_get_enriched_drug_populates_effects(tmp_path: Path):
    store = _empty_store(
        tmp_path,
        {
            "2244": [
                {
                    "name": "Nausea",
                    "meddra_code": "10028813",
                    "source": "SIDER",
                    "source_id": "CID100002244",
                }
            ],
        },
    )
    fake_props = {"CID": 2244, "Title": "aspirin", "MolecularFormula": "C9H8O4"}
    with patch("src.data.repository.fetch_compound", return_value=fake_props):
        drug = get_enriched_drug(2244, store)

    assert drug is not None
    assert drug.name == "aspirin"
    assert drug.side_effects[0].name == "Nausea"


def test_get_enriched_drug_unknown_cid_returns_none(tmp_path: Path):
    store = _empty_store(tmp_path)
    with patch("src.data.repository.fetch_compound", return_value=None):
        assert get_enriched_drug(2244, store) is None
