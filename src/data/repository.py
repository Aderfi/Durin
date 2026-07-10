"""Drug data access and enriched medication construction.

This is where the Drug<->Med enrichment lives, previously (wrongly) done inside a
`model_validator`. The models stay pure: they only validate. This module does the I/O
and reference resolution.
"""

from collections.abc import Iterable
from datetime import date

from src.data.pubchem import fetch_compound, fetch_compounds
from src.data.schemas.drug import ATCCode, Drug
from src.data.schemas.medication import Med


def _drug_from_props(props: dict) -> Drug | None:
    """Build a Drug from a PubChem property record, or None for an empty record.

    PubChem returns a record with only {"CID": n} (HTTP 200) for nonexistent CIDs.
    """
    name = props.get("Title") or props.get("MolecularFormula")
    if not name:
        return None
    return Drug(
        cid=props["CID"],
        name=name,
        molecular_formula=props.get("MolecularFormula"),
        smiles=props.get("SMILES"),
        inchikey=props.get("InChIKey"),
    )


def get_drug_by_cid(cid: int) -> Drug | None:
    """Return a Drug for its PubChem CID, or None if it cannot be resolved.

    Resolves name, formula, SMILES and InChIKey from PubChem. The ATC classification
    (`chemical_group`), side effects and interactions do not come from this endpoint
    and are left unpopulated (enriched through other paths).
    """
    props = fetch_compound(cid)
    return _drug_from_props(props) if props else None


def resolve_active_principles(cids: Iterable[int]) -> list[Drug]:
    """Resolve a list of CIDs to Drugs in a single batch, dropping unresolved ones.

    Input order is preserved.
    """
    cids = list(cids)
    table = fetch_compounds(cids)
    drugs = []
    for cid in cids:
        props = table.get(cid)
        if props is not None:
            drug = _drug_from_props(props)
            if drug is not None:
                drugs.append(drug)
    return drugs


def build_med(
    *,
    atc_code: str,
    name: str,
    dosage: str,
    frequency: str,
    start_date: date,
    end_date: date | None = None,
    cids: Iterable[int] = (),
    active_principles: list[Drug] | None = None,
) -> Med:
    """Build a Med.

    Pass explicit `active_principles`, or `cids` to resolve them via PubChem
    (`resolve_active_principles`). If both are given, they are concatenated.
    """
    principles = list(active_principles or [])
    principles.extend(resolve_active_principles(cids))
    return Med(
        ATC_code=ATCCode(code=atc_code),
        name=name,
        dosage=dosage,
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        active_principles=principles,
    )
