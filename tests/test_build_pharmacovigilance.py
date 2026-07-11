import json
from pathlib import Path

from scripts.build_pharmacovigilance import BuildInputs, build_datasets

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


def test_build_datasets_writes_three_files(tmp_path: Path):
    sider = tmp_path / "se.tsv"
    sider.write_text(_SE_ROWS, encoding="utf-8")
    twosides = tmp_path / "two.csv"
    twosides.write_text(_TWOSIDES_CSV, encoding="utf-8")
    chembl = tmp_path / "chembl.csv"
    chembl.write_text(_CHEMBL_CSV, encoding="utf-8")
    out = tmp_path / "out"

    build_datasets(
        BuildInputs(
            sider_se=sider,
            twosides=twosides,
            chembl_moa=chembl,
            unichem={"CHEMBL25": 2244},
            rxnorm_to_cid={"10355": 5391, "136411": 135398744},
        ),
        out,
    )

    effects = json.loads((out / "sider_effects.json").read_text())
    assert "2244" in effects
    assert json.loads((out / "twosides_ddi.json").read_text())["5391"]
    assert json.loads((out / "chembl_moa.json").read_text())["2244"]
