"""Offline ETL: build Tier 1 pharmacovigilance datasets keyed by PubChem CID.

Downloads (documented below) are performed separately; this script parses the
local files and writes the per-CID JSON datasets consumed at runtime by
``src.data.enrichment``. The LLM term-normalizer (openFDA text -> MedDRA) is NOT
invoked here yet — it is a placeholder (see pharmacovigilance/normalizer.py).

Source downloads:
  SIDER 4.1    : http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz
  TWOSIDES     : https://tatonetti.c2b2.columbia.edu/nsides/  (CSV export)
  ChEMBL MoA   : ChEMBL DB `mechanism` table export (CSV)
  UniChem map  : https://www.ebi.ac.uk/unichem/  (ChEMBL id -> PubChem CID)
"""

from __future__ import annotations

import argparse
import json
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

from src.data.pharmacovigilance import db  # noqa: E402
from src.data.sources import (  # noqa: E402
    iter_twosides_rows,
    parse_chembl_moa,
    parse_sider,
)
from src.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


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


_TWOSIDES_COMMIT_EVERY = 500_000  # rows inserted per SQLite transaction


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


def _stream_twosides_into_db(
    conn, twosides_path: Path, rxnorm_to_cid: dict[str, int]
) -> None:
    """Stream TWOSIDES rows into the DB in bounded batches (never all in RAM)."""
    batch: list[dict] = []
    total = 0
    for row in iter_twosides_rows(twosides_path, rxnorm_to_cid):
        batch.append(row)
        if len(batch) >= _TWOSIDES_COMMIT_EVERY:
            db.insert_rows(conn, "twosides_ddi", batch)
            conn.commit()
            total += len(batch)
            batch = []
    if batch:
        db.insert_rows(conn, "twosides_ddi", batch)
        conn.commit()
        total += len(batch)
    logger.info("Inserted %d TWOSIDES interaction rows", total)


def build_database(
    inputs: BuildInputs,
    db_path: Path,
    reactions_by_cid: dict[int, list[str]] | None = None,
) -> None:
    """Build the unified pharmacovigilance SQLite DB from all sources.

    Writes the four dataset tables keyed by CID. SIDER and ChEMBL are small and
    inserted whole; TWOSIDES is streamed in batches so it never loads into RAM.
    When ``reactions_by_cid`` is given, the local LLM normalizer codes the openFDA
    free text over the SIDER MedDRA vocabulary into ``openfda_effects`` (Tier 2).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(db_path)
    try:
        db.create_schema(conn)

        sider_by_cid = parse_sider(inputs.sider_se)
        db.insert_by_cid(conn, "sider_effects", sider_by_cid)
        db.insert_by_cid(
            conn, "chembl_moa", parse_chembl_moa(inputs.chembl_moa, inputs.unichem)
        )
        conn.commit()

        _stream_twosides_into_db(conn, inputs.twosides, inputs.rxnorm_to_cid)

        if reactions_by_cid:
            # Imported here so a Tier 1 build never pulls the heavy embedding stack.
            from src.data.pharmacovigilance.normalizer import (
                LocalLLMNormalizer,
                SapBERTCandidateGenerator,
            )

            vocab = build_meddra_vocab(sider_by_cid)
            normalizer = LocalLLMNormalizer(SapBERTCandidateGenerator(vocab))
            db.insert_by_cid(
                conn,
                "openfda_effects",
                normalize_openfda_effects(reactions_by_cid, normalizer),
            )
            conn.commit()
        logger.info("Built pharmacovigilance DB: %s", db_path)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Tier 1 pharmacovigilance datasets."
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
        "when given, they are coded to MedDRA offline into the openfda_effects table.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("src/data/pharmacovigilance/pharmacovigilance.db"),
        help="Output SQLite database path.",
    )
    args = parser.parse_args()
    unichem = _load_tsv_map(args.unichem)
    rxnorm_to_cid = _load_tsv_map(args.rxnorm)

    reactions_by_cid = None
    if args.openfda_reactions is not None:
        raw = json.loads(args.openfda_reactions.read_text(encoding="utf-8"))
        reactions_by_cid = {int(cid): phrases for cid, phrases in raw.items()}

    build_database(
        BuildInputs(
            args.sider_se, args.twosides, args.chembl_moa, unichem, rxnorm_to_cid
        ),
        args.out,
        reactions_by_cid,
    )


if __name__ == "__main__":
    main()

# Example:
#   python -m scripts.build_pharmacovigilance \
#     --sider-se tmp/meddra_all_se.tsv \
#     --twosides tmp/TWOSIDES.csv \
#     --chembl-moa tmp/chembl_moa.csv \
#     --unichem tmp/src1src22.txt \
#     --rxnorm tmp/rxnorm_to_cid.tsv \
#     --out src/data/pharmacovigilance/pharmacovigilance.db
