"""Offline ETL orchestration.

Wires up the existing pipeline scripts (ATC/ICD-10 scrapers, rxnorm map,
pharmacovigilance build) through the neo4j_import CSVs. Stops there on
purpose: `neo4j-admin database import` needs the Neo4j service stopped and
overwrites the database, so it stays a manual, deliberately-gated step (see
_management/2026-07-18-pharmacovigilance-neo4j-migration.md). TWOSIDES,
ChEMBL MoA, and UniChem have no stable file URL (portal/DB exports) and are
declared as manual inputs -- Snakemake's own MissingInputException already
names the exact missing file if they're absent.

See docs/superpowers/specs/2026-07-21-snakemake-etl-orchestration-design.md.
"""

configfile: "config.yaml"

NEO4J_IMPORT_FILES = [
    "drugs.csv",
    "adverse_effects.csv",
    "mechanisms.csv",
    "has_side_effect.csv",
    "has_mechanism.csv",
    "interacts_with_header.csv",
    "interacts_with.csv",
]


rule all:
    input:
        "src/data/atc/codes.json",
        "scripts/plain_icd_codes.json",
        expand(
            f"{config['neo4j_import_dir']}/{{f}}",
            f=NEO4J_IMPORT_FILES,
        ),


rule scrape_atc_catalog:
    output:
        "src/data/atc/codes.json",
    shell:
        "python scripts/atc_scraper.py "
        "--output {output} --checkpoint scripts/checkpoint.json"


rule scrape_icd10:
    output:
        "scripts/icd_codes.json",
    shell:
        "python scripts/icd10_scraper.py --output {output}"


rule flatten_icd10:
    input:
        "scripts/icd_codes.json",
    output:
        "scripts/plain_icd_codes.json",
    shell:
        "python scripts/json_plain.py --input {input} --output {output}"


rule download_sider:
    output:
        f"{config['tmp_dir']}/raw/meddra_all_se.tsv.gz",
    params:
        url=config["sider_url"],
    shell:
        "mkdir -p {config[tmp_dir]}/raw && curl -fsSL {params.url} -o {output}"


rule build_rxnorm_map:
    input:
        twosides=f"{config['tmp_dir']}/TWOSIDES.csv",
    output:
        f"{config['tmp_dir']}/rxnorm_to_cid.tsv",
    shell:
        "python scripts/build_rxnorm_map.py --twosides {input.twosides} --out {output}"


rule build_pharmacovigilance_csvs:
    input:
        sider=f"{config['tmp_dir']}/raw/meddra_all_se.tsv.gz",
        twosides=f"{config['tmp_dir']}/TWOSIDES.csv",
        chembl=f"{config['tmp_dir']}/chembl_moa.csv",
        unichem=f"{config['tmp_dir']}/src1src22.txt",
        rxnorm=f"{config['tmp_dir']}/rxnorm_to_cid.tsv",
    output:
        expand(
            f"{config['neo4j_import_dir']}/{{f}}",
            f=NEO4J_IMPORT_FILES,
        ),
    params:
        out_dir=config["neo4j_import_dir"],
    shell:
        "python -m scripts.build_pharmacovigilance "
        "--sider-se {input.sider} --twosides {input.twosides} "
        "--chembl-moa {input.chembl} --unichem {input.unichem} "
        "--rxnorm {input.rxnorm} --out-dir {params.out_dir}"
