from src.data.enrichment import enrich_drug
from src.data.schemas import Drug

_SIDER = {
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
_TWOSIDES = {
    "2244": [
        {
            "interacting_cid": 5090,
            "interacting_name": None,
            "mechanism": "Increased risk of bleeding",
            "meddra_pt": "Gastrointestinal haemorrhage",
            "meddra_code": None,
            "source": "TWOSIDES",
            "source_id": "2244-5090",
        }
    ],
}


def test_side_effects_assembled_with_provenance(pv_graph):
    make_store, _ = pv_graph
    store = make_store(sider=_SIDER, twosides=_TWOSIDES)
    effects = store.side_effects(2244)
    assert len(effects) == 1
    se = effects[0]
    assert se.meddra_code == "10017955"
    assert se.severity == "severe" and se.severity_derived is True
    assert se.provenance.source == "SIDER"


def test_interactions_assembled_with_provenance(pv_graph):
    make_store, _ = pv_graph
    store = make_store(sider=_SIDER, twosides=_TWOSIDES)
    inter = store.interactions(2244)
    assert len(inter) == 1
    assert inter[0].mechanism == "Increased risk of bleeding"
    assert inter[0].interacting_drug == "CID 5090"  # deterministic fallback identity
    assert inter[0].interacting_cid == 5090  # chemical identity for pairing
    assert inter[0].provenance.source == "TWOSIDES"


def test_interactions_found_from_either_endpoint(pv_graph):
    """The single de-duplicated edge is matched undirected."""
    make_store, _ = pv_graph
    store = make_store(twosides=_TWOSIDES)
    # 2244 is the START, 5090 the END; querying the END still finds the edge.
    inter = store.interactions(5090)
    assert len(inter) == 1
    assert inter[0].interacting_cid == 2244


def test_enrich_drug_populates_lists(pv_graph):
    make_store, _ = pv_graph
    store = make_store(sider=_SIDER, twosides=_TWOSIDES)
    drug = Drug(cid=2244, name="aspirin")
    enriched = enrich_drug(drug, store)
    assert len(enriched.side_effects) == 1
    assert len(enriched.interactions) == 1
    # Original is untouched (models only validate; enrichment returns a copy).
    assert drug.side_effects == []


def test_unknown_cid_returns_empty(pv_graph):
    make_store, _ = pv_graph
    store = make_store(sider=_SIDER, twosides=_TWOSIDES)
    assert store.side_effects(999999) == []
    assert store.interactions(999999) == []


def test_sider_effect_keyed_by_umls_cui(pv_graph):
    """SIDER effects carry a UMLS CUI, not a MedDRA code (no severity signal)."""
    make_store, _ = pv_graph
    store = make_store(
        sider={
            "2244": [
                {
                    "name": "Abdominal cramps",
                    "meddra_pt": "Abdominal cramps",
                    "umls_cui": "C0000729",
                    "frequency": None,
                    "source": "SIDER",
                    "source_id": "CID100002244",
                }
            ]
        }
    )
    effects = store.side_effects(2244)
    assert len(effects) == 1
    se = effects[0]
    assert se.umls_cui == "C0000729"
    assert se.meddra_code is None
    # No numeric MedDRA code -> no derived severity.
    assert se.severity is None and se.severity_derived is False


def test_side_effects_merges_openfda(pv_graph):
    make_store, _ = pv_graph
    store = make_store(
        sider={
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
        },
        openfda={
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
        },
    )
    effects = store.side_effects(2244)
    assert {e.provenance.source for e in effects} == {"SIDER", "LLM_NORMALIZED"}
    llm = next(e for e in effects if e.provenance.source == "LLM_NORMALIZED")
    assert llm.meddra_code == "10028813"
    assert llm.provenance.source_id == "nausea"
