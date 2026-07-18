"""Offline ETL: build the Tier 1 pharmacovigilance graph keyed by PubChem CID.

Parses the local source files (downloaded separately — see below) and emits the
CSV files consumed by ``neo4j-admin database import``, which bulk-loads them into
the local Neo4j graph read at runtime by ``src.data.enrichment``. The output is a
graph, not a table dump:

    (:Drug {cid, name})
    (:AdverseEffect {code, coding_system, meddra_pt})
    (:Mechanism {key, mechanism, action_type})
    (:Drug)-[:HAS_SIDE_EFFECT {name, frequency, source, source_id}]->(:AdverseEffect)
    (:Drug)-[:INTERACTS_WITH {mechanism, conditions, prr, source, source_id}]-(:Drug)
    (:Drug)-[:HAS_MECHANISM {source, source_id}]->(:Mechanism)

Node sets (drugs, effects, mechanisms) are small and de-duplicated in memory.
TWOSIDES is large (~89M rows); its interactions are de-duplicated to one edge per
unordered pair by a streaming Polars pipeline that never loads the file into RAM.

Source downloads:
  SIDER 4.1    : http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz
  TWOSIDES     : https://tatonetti.c2b2.columbia.edu/nsides/  (CSV export)
  ChEMBL MoA   : ChEMBL DB `mechanism` table export (CSV)
  UniChem map  : https://www.ebi.ac.uk/unichem/  (ChEMBL id -> PubChem CID)
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.pharmacovigilance.normalizer import TermNormalizer

ROOT = Path.cwd().parent  # el notebook vive en notebooks/ -> raíz del repo
sys.path.insert(
    0, str(ROOT)
)  # para poder importar src.data.sources y src.utils.logging

import polars as pl  # noqa: E402

from src.data.pharmacovigilance import graph  # noqa: E402
from src.data.sources import (  # noqa: E402
    parse_chembl_moa,
    parse_sider,
)
from src.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# Array-property delimiter for neo4j-admin (list columns like `conditions`).
_ARRAY_DELIMITER = "|"


@dataclass
class BuildInputs:
    """Local input files for the ETL (downloaded beforehand)."""

    sider_se: Path
    twosides: Path
    chembl_moa: Path
    unichem: dict[str, int]  # ChEMBL id -> PubChem CID
    rxnorm_to_cid: dict[str, int]  # RxNorm id -> PubChem CID (for TWOSIDES)


def _load_tsv_map(path: Path) -> dict[str, int]:
    """Load a two-column ``id\\tcid`` TSV into ``{id: cid}``.

    Skips any header/malformed line whose second column is not an integer, so
    UniChem's header row is tolerated.
    """
    mapping: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[1].strip().isdigit():
            continue
        mapping[parts[0].strip()] = int(parts[1].strip())
    logger.info("Loaded %d id->CID mappings from %s", len(mapping), path)
    return mapping


def build_meddra_vocab(sider_by_cid: dict[int, list[dict]]) -> dict[str, str]:
    """Build the closed MedDRA vocabulary (Preferred Term -> code) from SIDER.

    This is the vocabulary the term normalizer maps openFDA free text into, so
    the LLM codes against the same terms already present in the Tier 1 data.
    """
    vocab: dict[str, str] = {}
    for rows in sider_by_cid.values():
        for row in rows:
            pt, code = row.get("meddra_pt"), row.get("meddra_code")
            if pt and code:
                vocab.setdefault(pt, code)
    return vocab


def normalize_openfda_effects(
    reactions_by_cid: dict[int, list[str]],
    normalizer: TermNormalizer,
) -> dict[str, list[dict]]:
    """Code openFDA free-text reactions to MedDRA via the term normalizer.

    Offline ETL only -- the LLM is confined here; runtime never calls it. Each
    phrase the normalizer maps yields a row tagged ``source="LLM_NORMALIZED"``
    with the original text preserved in ``source_id``. Unmapped phrases are
    dropped (precision-first: never fabricate a code).
    """
    out: dict[str, list[dict]] = {}
    coded = dropped = 0
    for cid, phrases in reactions_by_cid.items():
        for phrase in phrases:
            result = normalizer.normalize(phrase)
            if result is None:
                dropped += 1
                continue
            pt, code = result
            coded += 1
            out.setdefault(str(cid), []).append(
                {
                    "name": phrase,
                    "meddra_pt": pt,
                    "meddra_code": code,
                    "frequency": None,
                    "source": "LLM_NORMALIZED",
                    "source_id": phrase,
                }
            )
    logger.info("Normalized openFDA effects: %d coded, %d dropped", coded, dropped)
    return out


# --- CSV emission -----------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    """Write a header + rows CSV (small node/edge files)."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _mechanism_id(mechanism: str, action_type: str | None) -> str:
    """Synthetic node id for a (mechanism, action_type) pair."""
    return f"{mechanism}||{action_type or ''}"


def _twosides_drug_names(inputs: BuildInputs) -> dict[int, str]:
    """Collect ``{cid: concept_name}`` for the (small) TWOSIDES drug set.

    TWOSIDES references only a few thousand distinct drugs, so this fits in RAM
    even though the interaction rows do not.
    """
    rx = pl.LazyFrame(
        {"rx": list(inputs.rxnorm_to_cid), "cid": list(inputs.rxnorm_to_cid.values())},
        schema={"rx": pl.Utf8, "cid": pl.Int64},
    )
    scan = pl.scan_csv(inputs.twosides, infer_schema_length=0)
    left = scan.join(
        rx.rename({"rx": "drug_1_rxnorn_id", "cid": "cid"}),
        on="drug_1_rxnorn_id",
        how="inner",
    ).select(cid="cid", name="drug_1_concept_name")
    right = scan.join(
        rx.rename({"rx": "drug_2_rxnorm_id", "cid": "cid"}),
        on="drug_2_rxnorm_id",
        how="inner",
    ).select(cid="cid", name="drug_2_concept_name")
    frame = pl.concat([left, right]).unique(subset="cid").collect()
    return dict(zip(frame["cid"].to_list(), frame["name"].to_list(), strict=True))


def _emit_node_and_edge_csvs(
    inputs: BuildInputs,
    out_dir: Path,
    reactions_by_cid: dict[int, list[str]] | None,
) -> dict[int, list[dict]]:
    """Emit Drug/AdverseEffect/Mechanism nodes and their (non-TWOSIDES) edges.

    Returns the parsed SIDER map so the caller can build the MedDRA vocab.
    """
    sider_by_cid = parse_sider(inputs.sider_se)
    chembl_by_cid = parse_chembl_moa(inputs.chembl_moa, inputs.unichem)

    openfda_by_cid: dict[str, list[dict]] = {}
    if reactions_by_cid:
        # Imported here so a Tier 1 build never pulls the heavy embedding stack.
        from src.data.pharmacovigilance.normalizer import (
            LocalLLMNormalizer,
            SapBERTCandidateGenerator,
        )

        vocab = build_meddra_vocab(sider_by_cid)
        normalizer = LocalLLMNormalizer(SapBERTCandidateGenerator(vocab))
        openfda_by_cid = normalize_openfda_effects(reactions_by_cid, normalizer)

    # Adverse-effect nodes + HAS_SIDE_EFFECT edges. Each node is keyed by ``code``
    # -- a numeric MedDRA code (normalizer) or a UMLS CUI (SIDER, which has no
    # MedDRA code) -- tagged with its coding_system so the runtime can tell them
    # apart. The two id spaces do not collide (numeric vs "C####...").
    effects: dict[str, tuple[str, str]] = {}  # code -> (meddra_pt, coding_system)
    se_edges: list[list] = []
    effect_sources = list(sider_by_cid.items()) + [
        (int(cid), rows) for cid, rows in openfda_by_cid.items()
    ]
    for cid, rows in effect_sources:
        for row in rows:
            meddra_code = row.get("meddra_code")
            if meddra_code:
                code, system = meddra_code, "MEDDRA"
            elif row.get("umls_cui"):
                code, system = row["umls_cui"], "UMLS_CUI"
            else:
                continue  # cannot key an AdverseEffect node without any code
            effects.setdefault(code, (row.get("meddra_pt") or row["name"], system))
            se_edges.append(
                [
                    cid,
                    code,
                    row["name"],
                    row.get("frequency") or "",
                    row["source"],
                    row.get("source_id") or "",
                ]
            )

    # Mechanism nodes (keyed by mechanism+action_type) + HAS_MECHANISM edges.
    mechanisms: dict[str, tuple[str, str]] = {}  # id -> (mechanism, action_type)
    moa_edges: list[list] = []
    for cid, rows in chembl_by_cid.items():
        for row in rows:
            mech, action = row["mechanism"], row.get("action_type") or ""
            mid = _mechanism_id(mech, action)
            mechanisms.setdefault(mid, (mech, action))
            moa_edges.append([cid, mid, row["source"], row.get("source_id") or ""])

    # Drug nodes = union of every CID referenced by any source, named where known.
    drug_names = _twosides_drug_names(inputs)
    drug_cids: set[int] = set(sider_by_cid) | set(chembl_by_cid) | set(drug_names)
    drug_cids |= {int(c) for c in openfda_by_cid}

    _write_csv(
        out_dir / "drugs.csv",
        [":ID(Drug)", "cid:long", "name"],
        [[cid, cid, drug_names.get(cid, "")] for cid in sorted(drug_cids)],
    )
    _write_csv(
        out_dir / "adverse_effects.csv",
        [":ID(Effect)", "code", "meddra_pt", "coding_system"],
        [[code, code, pt, system] for code, (pt, system) in sorted(effects.items())],
    )
    _write_csv(
        out_dir / "mechanisms.csv",
        [":ID(Mechanism)", "key", "mechanism", "action_type"],
        [
            [mid, mid, mech, action]
            for mid, (mech, action) in sorted(mechanisms.items())
        ],
    )
    _write_csv(
        out_dir / "has_side_effect.csv",
        [
            ":START_ID(Drug)",
            ":END_ID(Effect)",
            "name",
            "frequency",
            "source",
            "source_id",
        ],
        se_edges,
    )
    _write_csv(
        out_dir / "has_mechanism.csv",
        [":START_ID(Drug)", ":END_ID(Mechanism)", "source", "source_id"],
        moa_edges,
    )
    logger.info(
        "Emitted %d drugs, %d effects, %d mechanisms, %d SE edges, %d MoA edges",
        len(drug_cids),
        len(effects),
        len(mechanisms),
        len(se_edges),
        len(moa_edges),
    )
    return sider_by_cid


def _emit_interactions_csv(inputs: BuildInputs, out_dir: Path) -> None:
    """Stream TWOSIDES into a de-duplicated INTERACTS_WITH edge file (one/pair).

    Canonicalizes each pair to ``(min, max)`` CID and aggregates every condition
    into a single edge. Runs on the Polars streaming engine so the ~89M source
    rows are never all in memory. A header file is written separately; the data
    file is header-less (neo4j-admin ``header.csv,data.csv`` form).
    """
    _write_csv(
        out_dir / "interacts_with_header.csv",
        [
            ":START_ID(Drug)",
            ":END_ID(Drug)",
            "mechanism",
            "conditions:string[]",
            "prr:string[]",
            "source",
            "source_id",
        ],
        [],
    )

    rx = pl.LazyFrame(
        {"rx": list(inputs.rxnorm_to_cid), "cid": list(inputs.rxnorm_to_cid.values())},
        schema={"rx": pl.Utf8, "cid": pl.Int64},
    )
    lf = (
        pl.scan_csv(inputs.twosides, infer_schema_length=0)
        .join(
            rx.rename({"rx": "drug_1_rxnorn_id", "cid": "c1"}),
            on="drug_1_rxnorn_id",
            how="inner",
        )
        .join(
            rx.rename({"rx": "drug_2_rxnorm_id", "cid": "c2"}),
            on="drug_2_rxnorm_id",
            how="inner",
        )
        .with_columns(
            a=pl.min_horizontal("c1", "c2"),
            b=pl.max_horizontal("c1", "c2"),
        )
        .filter(pl.col("a") != pl.col("b"))  # drop self-interactions
        .group_by("a", "b")
        .agg(
            conditions=pl.col("condition_concept_name").unique(),
            prr=pl.col("PRR"),
        )
        .with_columns(
            mechanism=(
                pl.lit("Increased risk of: ")
                + pl.col("conditions").list.join(", ")
                + pl.lit(" (TWOSIDES)")
            ),
            conditions=pl.col("conditions").list.join(_ARRAY_DELIMITER),
            prr=pl.col("prr").list.join(_ARRAY_DELIMITER),
            source=pl.lit("TWOSIDES"),
            source_id=(
                pl.col("a").cast(pl.Utf8) + pl.lit("-") + pl.col("b").cast(pl.Utf8)
            ),
        )
        .select("a", "b", "mechanism", "conditions", "prr", "source", "source_id")
    )
    out_file = out_dir / "interacts_with.csv"
    lf.sink_csv(out_file, include_header=False)
    logger.info("Streamed de-duplicated TWOSIDES interactions -> %s", out_file)


def build_import_csvs(
    inputs: BuildInputs,
    out_dir: Path,
    reactions_by_cid: dict[int, list[str]] | None = None,
) -> None:
    """Emit every node/relationship CSV for ``neo4j-admin database import``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit_node_and_edge_csvs(inputs, out_dir, reactions_by_cid)
    _emit_interactions_csv(inputs, out_dir)
    logger.info("Built pharmacovigilance import CSVs in %s", out_dir)


def import_command(out_dir: Path, database: str) -> list[str]:
    """Build the ``neo4j-admin database import full`` argv for ``out_dir``."""
    d = out_dir
    return [
        "neo4j-admin",
        "database",
        "import",
        "full",
        database,
        f"--nodes={graph.DRUG}={d / 'drugs.csv'}",
        f"--nodes={graph.ADVERSE_EFFECT}={d / 'adverse_effects.csv'}",
        f"--nodes={graph.MECHANISM}={d / 'mechanisms.csv'}",
        f"--relationships={graph.HAS_SIDE_EFFECT}={d / 'has_side_effect.csv'}",
        f"--relationships={graph.HAS_MECHANISM}={d / 'has_mechanism.csv'}",
        f"--relationships={graph.INTERACTS_WITH}="
        f"{d / 'interacts_with_header.csv'},{d / 'interacts_with.csv'}",
        "--id-type=string",
        f"--array-delimiter={_ARRAY_DELIMITER}",
        "--overwrite-destination",
    ]


def run_import(out_dir: Path, database: str) -> None:
    """Invoke ``neo4j-admin`` to load the CSVs (server must be STOPPED)."""
    cmd = import_command(out_dir, database)
    logger.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Tier 1 pharmacovigilance graph import CSVs."
    )
    parser.add_argument("--sider-se", type=Path, required=True)
    parser.add_argument("--twosides", type=Path, required=True)
    parser.add_argument("--chembl-moa", type=Path, required=True)
    parser.add_argument(
        "--unichem",
        type=Path,
        required=True,
        help="TSV mapping ChEMBL id -> PubChem CID (UniChem src1src22).",
    )
    parser.add_argument(
        "--rxnorm",
        type=Path,
        required=True,
        help="TSV mapping RxNorm id -> PubChem CID (for TWOSIDES).",
    )
    parser.add_argument(
        "--openfda-reactions",
        type=Path,
        default=None,
        help="Optional JSON mapping CID -> list of free-text reaction phrases; "
        "when given, they are coded to MedDRA offline into HAS_SIDE_EFFECT edges.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("tmp/neo4j_import"),
        help="Directory to write the neo4j-admin import CSVs into.",
    )
    parser.add_argument(
        "--run-import",
        action="store_true",
        help="Also invoke `neo4j-admin database import` (the server must be "
        "STOPPED; this overwrites the target database).",
    )
    parser.add_argument(
        "--database",
        default="neo4j",
        help="Target Neo4j database (Community edition: always 'neo4j').",
    )
    args = parser.parse_args()
    unichem = _load_tsv_map(args.unichem)
    rxnorm_to_cid = _load_tsv_map(args.rxnorm)

    reactions_by_cid = None
    if args.openfda_reactions is not None:
        raw = json.loads(args.openfda_reactions.read_text(encoding="utf-8"))
        reactions_by_cid = {int(cid): phrases for cid, phrases in raw.items()}

    inputs = BuildInputs(
        args.sider_se, args.twosides, args.chembl_moa, unichem, rxnorm_to_cid
    )
    build_import_csvs(inputs, args.out_dir, reactions_by_cid)

    if args.run_import:
        run_import(args.out_dir, args.database)
    else:
        logger.info(
            "CSVs ready. Stop the server, then run:\n  %s",
            " ".join(import_command(args.out_dir, args.database)),
        )


if __name__ == "__main__":
    main()

# Example:
#   1) python -m scripts.build_pharmacovigilance \
#        --sider-se tmp/meddra_all_se.tsv \
#        --twosides tmp/TWOSIDES.csv \
#        --chembl-moa tmp/chembl_moa.csv \
#        --unichem tmp/src1src22.txt \
#        --rxnorm tmp/rxnorm_to_cid.tsv \
#        --out-dir tmp/neo4j_import
#   2) sudo systemctl stop neo4j
#   3) neo4j-admin database import full neo4j --nodes=... (printed above)
#   4) sudo systemctl start neo4j   # then apply constraints (setup_constraints)
