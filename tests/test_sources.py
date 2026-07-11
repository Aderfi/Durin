import logging
from pathlib import Path

from src.data.sources import (
    parse_chembl_moa,
    parse_sider,
    parse_twosides,
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
    first = next(e for e in effects if e["meddra_code"] == "10017955")
    assert first["name"] == "Gastrointestinal haemorrhage"
    assert first["source"] == "SIDER"
    assert first["source_id"] == "CID100002244"


# Minimal TWOSIDES CSV: drug_1 CID, drug_2 CID, condition MedDRA name, PRR.
_TWOSIDES_CSV = (
    "drug_1_cid,drug_2_cid,condition_meddra_name,prr\n"
    "2244,5090,Gastrointestinal haemorrhage,4.2\n"
)


def test_parse_twosides_symmetric(tmp_path):
    p = tmp_path / "twosides.csv"
    p.write_text(_TWOSIDES_CSV, encoding="utf-8")
    result = parse_twosides(p)
    # Interaction indexed under both members of the pair.
    assert 2244 in result and 5090 in result
    entry = result[2244][0]
    assert entry["interacting_cid"] == 5090
    assert entry["meddra_pt"] == "Gastrointestinal haemorrhage"
    assert entry["source"] == "TWOSIDES"


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
