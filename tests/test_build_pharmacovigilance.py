from pathlib import Path

from scripts.build_pharmacovigilance import BuildInputs, build_database
from src.data.pharmacovigilance import db as pvdb

_SE_ROWS = (
    "CID100002244\tCID000002244\tC0018939\tPT\t10017955\tGastrointestinal haemorrhage\n"
)
_TWOSIDES_CSV = (
    "drug_1_rxnorn_id,drug_1_concept_name,drug_2_rxnorm_id,drug_2_concept_name,"
    "condition_meddra_id,condition_concept_name,A,B,C,D,PRR,PRR_error,"
    "mean_reporting_frequency\n"
    "10355,Temazepam,136411,sildenafil,10003239,Nausea,7,149,24,1536,"
    "3.1,0.42,0.04\n"
)
_CHEMBL_CSV = (
    "molecule_chembl_id,mechanism_of_action,action_type\n"
    "CHEMBL25,COX inhibitor,INHIBITOR\n"
)


def _count(conn, table: str, cid: int) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE cid = ?", (cid,)
    ).fetchone()[0]


def test_build_database_populates_all_tables(tmp_path: Path):
    sider = tmp_path / "se.tsv"
    sider.write_text(_SE_ROWS, encoding="utf-8")
    twosides = tmp_path / "two.csv"
    twosides.write_text(_TWOSIDES_CSV, encoding="utf-8")
    chembl = tmp_path / "chembl.csv"
    chembl.write_text(_CHEMBL_CSV, encoding="utf-8")
    db_path = tmp_path / "pv.db"

    build_database(
        BuildInputs(
            sider_se=sider,
            twosides=twosides,
            chembl_moa=chembl,
            unichem={"CHEMBL25": 2244},
            rxnorm_to_cid={"10355": 5391, "136411": 135398744},
        ),
        db_path,
    )

    conn = pvdb.connect(db_path)
    assert _count(conn, "sider_effects", 2244) == 1
    # TWOSIDES row is emitted under both members of the pair.
    assert _count(conn, "twosides_ddi", 5391) == 1
    assert _count(conn, "twosides_ddi", 135398744) == 1
    assert _count(conn, "chembl_moa", 2244) == 1


def test_build_meddra_vocab_dedupes():
    from scripts.build_pharmacovigilance import build_meddra_vocab

    sider = {
        2244: [
            {"meddra_pt": "Nausea", "meddra_code": "10028813"},
            {"meddra_pt": "Nausea", "meddra_code": "10028813"},
        ],
        5090: [{"meddra_pt": "Headache", "meddra_code": "10019211"}],
    }
    assert build_meddra_vocab(sider) == {
        "Nausea": "10028813",
        "Headache": "10019211",
    }


class _FakeNormalizer:
    """Codes only the exact phrase 'nausea'; everything else is unmappable."""

    def normalize(self, text: str):
        return ("Nausea", "10028813") if text.lower() == "nausea" else None


def test_normalize_openfda_effects_codes_and_drops():
    from scripts.build_pharmacovigilance import normalize_openfda_effects

    out = normalize_openfda_effects(
        {2244: ["Nausea", "unmappable gibberish"]}, _FakeNormalizer()
    )
    assert out == {
        "2244": [
            {
                "name": "Nausea",
                "meddra_pt": "Nausea",
                "meddra_code": "10028813",
                "frequency": None,
                "source": "LLM_NORMALIZED",
                "source_id": "Nausea",
            }
        ]
    }
