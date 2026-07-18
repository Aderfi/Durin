import json
import logging
from pathlib import Path

from src.data.sources import (
    derive_severity,
    fetch_openfda_label,
    iter_twosides_rows,
    parse_chembl_moa,
    parse_sider,
    stitch_to_cid,
)


def test_stitch_to_cid_flat():
    assert stitch_to_cid("CID100002244") == 2244


def test_stitch_to_cid_stereo():
    assert stitch_to_cid("CID000002244") == 2244


def test_stitch_to_cid_malformed_logs_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert stitch_to_cid("XYZ123") is None
    assert any("STITCH" in r.message for r in caplog.records)


# SIDER meddra_all_se.tsv columns:
# STITCH_flat, STITCH_stereo, UMLS_label, MedDRA_type, UMLS_meddra, side_effect_name
_SE_ROWS = (
    "CID100002244\tCID000002244\tC0018939\tPT\t10017955\tGastrointestinal haemorrhage\n"
    "CID100002244\tCID000002244\tC0027497\tPT\t10028813\tNausea\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_sider_groups_by_cid(tmp_path):
    se = _write(tmp_path, "se.tsv", _SE_ROWS)
    result = parse_sider(se)
    assert set(result) == {2244}
    effects = result[2244]
    assert len(effects) == 2
    # SIDER's fifth column is a UMLS CUI, surfaced as `umls_cui` (not a MedDRA code).
    first = next(e for e in effects if e["umls_cui"] == "10017955")
    assert "meddra_code" not in first
    assert first["name"] == "Gastrointestinal haemorrhage"
    assert first["source"] == "SIDER"
    assert first["source_id"] == "CID100002244"


# Real TWOSIDES header (note the misspelled drug_1_rxnorn_id).
_TWOSIDES_CSV = (
    "drug_1_rxnorn_id,drug_1_concept_name,drug_2_rxnorm_id,drug_2_concept_name,"
    "condition_meddra_id,condition_concept_name,A,B,C,D,PRR,PRR_error,"
    "mean_reporting_frequency\n"
    "10355,Temazepam,136411,sildenafil,10003239,Arthralgia,7,149,24,1536,"
    "2.91667,0.421275,0.0448718\n"
)

# RxNorm -> PubChem CID map (supplied to the ETL, analogous to the ChEMBL unichem map).
_RXNORM_TO_CID = {"10355": 5391, "136411": 135398744}


def test_iter_twosides_rows_symmetric(tmp_path):
    p = tmp_path / "twosides.csv"
    p.write_text(_TWOSIDES_CSV, encoding="utf-8")
    rows = list(iter_twosides_rows(p, _RXNORM_TO_CID))
    # Interaction emitted under both members of the pair.
    by_cid = {r["cid"]: r for r in rows}
    assert set(by_cid) == {5391, 135398744}
    entry = by_cid[5391]
    assert entry["interacting_cid"] == 135398744
    assert entry["interacting_name"] == "sildenafil"
    assert entry["meddra_pt"] == "Arthralgia"
    assert entry["meddra_code"] == "10003239"
    assert entry["source"] == "TWOSIDES"
    assert "PRR=2.91667" in entry["mechanism"]


def test_iter_twosides_rows_drops_unmapped(tmp_path):
    p = tmp_path / "twosides.csv"
    p.write_text(_TWOSIDES_CSV, encoding="utf-8")
    rows = list(iter_twosides_rows(p, {"10355": 5391}))  # 136411 unmapped
    assert rows == []


# Minimal ChEMBL mechanism CSV: molecule_chembl_id, mechanism_of_action, action_type.
_CHEMBL_CSV = (
    "molecule_chembl_id,mechanism_of_action,action_type\n"
    "CHEMBL25,Cyclooxygenase inhibitor,INHIBITOR\n"
)


def test_parse_chembl_moa_maps_to_cid(tmp_path):
    p = tmp_path / "chembl.csv"
    p.write_text(_CHEMBL_CSV, encoding="utf-8")
    result = parse_chembl_moa(p, unichem={"CHEMBL25": 2244})
    assert 2244 in result
    moa = result[2244][0]
    assert moa["mechanism"] == "Cyclooxygenase inhibitor"
    assert moa["action_type"] == "INHIBITOR"
    assert moa["source"] == "ChEMBL"


def test_parse_chembl_moa_skips_unmapped(tmp_path, caplog):
    p = tmp_path / "chembl.csv"
    p.write_text(_CHEMBL_CSV, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = parse_chembl_moa(p, unichem={})  # no mapping for CHEMBL25
    assert result == {}
    assert any("CHEMBL25" in r.message for r in caplog.records)


def test_fetch_openfda_uses_cache(tmp_path):
    cache = tmp_path / "_cache"
    cache.mkdir()
    (cache / "2244.json").write_text(
        json.dumps(
            {
                "adverse_reactions": "GI bleeding.",
                "mechanism_of_action": "COX inhibition.",
                "source_id": "cached",
            }
        ),
        encoding="utf-8",
    )
    # No network: cache hit returns the stored record.
    result = fetch_openfda_label(2244, "aspirin", cache_dir=cache)
    assert result["adverse_reactions"] == "GI bleeding."
    assert result["source_id"] == "cached"


def test_derive_severity_serious_flag():
    assert derive_severity("10017955", is_serious=True) == ("severe", True)


def test_derive_severity_coded_not_serious():
    assert derive_severity("10028813", is_serious=False) == ("moderate", True)


def test_derive_severity_no_signal():
    assert derive_severity(None, is_serious=False) == (None, False)


def test_split_adverse_reactions_segments_and_dedupes():
    from src.data.sources import split_adverse_reactions

    assert split_adverse_reactions("Nausea, vomiting; headache.\nNausea") == [
        "Nausea",
        "vomiting",
        "headache",
    ]


def test_split_adverse_reactions_empty():
    from src.data.sources import split_adverse_reactions

    assert split_adverse_reactions(None) == []
    assert split_adverse_reactions("") == []
