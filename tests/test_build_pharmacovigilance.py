import csv
from pathlib import Path

from scripts.build_pharmacovigilance import BuildInputs, build_import_csvs

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


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_build_import_csvs_emits_all_files(tmp_path: Path):
    sider = tmp_path / "se.tsv"
    sider.write_text(_SE_ROWS, encoding="utf-8")
    twosides = tmp_path / "two.csv"
    twosides.write_text(_TWOSIDES_CSV, encoding="utf-8")
    chembl = tmp_path / "chembl.csv"
    chembl.write_text(_CHEMBL_CSV, encoding="utf-8")
    out = tmp_path / "import"

    build_import_csvs(
        BuildInputs(
            sider_se=sider,
            twosides=twosides,
            chembl_moa=chembl,
            unichem={"CHEMBL25": 2244},
            rxnorm_to_cid={"10355": 5391, "136411": 135398744},
        ),
        out,
    )

    # Drug nodes: SIDER cid 2244 + ChEMBL cid 2244 + both TWOSIDES cids.
    drug_cids = {int(r["cid:long"]) for r in _rows(out / "drugs.csv")}
    assert {2244, 5391, 135398744} <= drug_cids

    # One adverse-effect node keyed by SIDER's UMLS CUI + its HAS_SIDE_EFFECT edge.
    effects = _rows(out / "adverse_effects.csv")
    assert any(
        r["code"] == "10017955" and r["coding_system"] == "UMLS_CUI" for r in effects
    )
    se_edges = _rows(out / "has_side_effect.csv")
    assert any(
        r[":START_ID(Drug)"] == "2244" and r[":END_ID(Effect)"] == "10017955"
        for r in se_edges
    )

    # One Mechanism node + HAS_MECHANISM edge from ChEMBL.
    assert any(r["mechanism"] == "COX inhibitor" for r in _rows(out / "mechanisms.csv"))
    assert any(r[":START_ID(Drug)"] == "2244" for r in _rows(out / "has_mechanism.csv"))

    # Single de-duplicated INTERACTS_WITH edge (canonical min/max pair), no header.
    with (out / "interacts_with.csv").open(encoding="utf-8", newline="") as fh:
        ddi = list(csv.reader(fh))
    assert len(ddi) == 1
    start, end = ddi[0][0], ddi[0][1]
    assert {start, end} == {"5391", "135398744"}
    assert int(start) < int(end)  # canonical ordering


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
