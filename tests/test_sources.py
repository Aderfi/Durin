import logging
from pathlib import Path

from src.data.sources import parse_sider, stitch_to_cid


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
