import json
from pathlib import Path

from src.data.enrichment import PharmacovigilanceStore, enrich_drug
from src.data.schemas import Drug


def _store(tmp_path: Path) -> PharmacovigilanceStore:
    (tmp_path / "sider_effects.json").write_text(
        json.dumps(
            {
                "2244": [
                    {
                        "name": "Gastrointestinal haemorrhage",
                        "meddra_pt": "Gastrointestinal haemorrhage",
                        "meddra_code": "10017955",
                        "frequency": "rare",
                        "source": "SIDER",
                        "source_id": "CID100002244",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "twosides_ddi.json").write_text(
        json.dumps(
            {
                "2244": [
                    {
                        "interacting_cid": 5090,
                        "mechanism": "Increased risk of bleeding",
                        "meddra_pt": "Gastrointestinal haemorrhage",
                        "source": "TWOSIDES",
                        "source_id": "2244-5090",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "chembl_moa.json").write_text(json.dumps({}), encoding="utf-8")
    return PharmacovigilanceStore(data_dir=tmp_path)


def test_side_effects_assembled_with_provenance(tmp_path):
    store = _store(tmp_path)
    effects = store.side_effects(2244)
    assert len(effects) == 1
    se = effects[0]
    assert se.meddra_code == "10017955"
    assert se.severity == "severe" and se.severity_derived is True
    assert se.provenance.source == "SIDER"


def test_interactions_assembled_with_provenance(tmp_path):
    store = _store(tmp_path)
    inter = store.interactions(2244)
    assert len(inter) == 1
    assert inter[0].mechanism == "Increased risk of bleeding"
    assert inter[0].interacting_drug == "CID 5090"  # deterministic fallback identity
    assert inter[0].interacting_cid == 5090  # chemical identity for pairing
    assert inter[0].provenance.source == "TWOSIDES"


def test_enrich_drug_populates_lists(tmp_path):
    store = _store(tmp_path)
    drug = Drug(cid=2244, name="aspirin")
    enriched = enrich_drug(drug, store)
    assert len(enriched.side_effects) == 1
    assert len(enriched.interactions) == 1
    # Original is untouched (models only validate; enrichment returns a copy).
    assert drug.side_effects == []


def test_unknown_cid_returns_empty(tmp_path):
    store = _store(tmp_path)
    assert store.side_effects(999999) == []
    assert store.interactions(999999) == []


def test_side_effects_merges_openfda(tmp_path):
    (tmp_path / "sider_effects.json").write_text(
        json.dumps(
            {
                "2244": [
                    {
                        "name": "Gastrointestinal haemorrhage",
                        "meddra_pt": "Gastrointestinal haemorrhage",
                        "meddra_code": "10017955",
                        "frequency": None,
                        "source": "SIDER",
                        "source_id": "CID100002244",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "openfda_effects.json").write_text(
        json.dumps(
            {
                "2244": [
                    {
                        "name": "nausea",
                        "meddra_pt": "Nausea",
                        "meddra_code": "10028813",
                        "frequency": None,
                        "source": "LLM_NORMALIZED",
                        "source_id": "nausea",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = PharmacovigilanceStore(data_dir=tmp_path)
    effects = store.side_effects(2244)
    assert {e.provenance.source for e in effects} == {"SIDER", "LLM_NORMALIZED"}
    llm = next(e for e in effects if e.provenance.source == "LLM_NORMALIZED")
    assert llm.meddra_code == "10028813"
    assert llm.provenance.source_id == "nausea"
