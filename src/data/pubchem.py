"""Lightweight client for the PubChem PUG REST API.

Resolves a CID (or several) to compound properties: name, molecular formula,
SMILES and InChIKey. Transient failures (connection/timeout/5xx) are retried and
single-CID lookups are cached in memory.

Docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""

import logging
from collections.abc import Iterable
from functools import lru_cache

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_PROPERTIES = "Title,MolecularFormula,SMILES,InChIKey"
_TIMEOUT = 15  # seconds


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _request(url: str) -> requests.Response | None:
    """GET with retries. Returns None on 404 (no retry); 5xx is retried."""
    resp = requests.get(url, timeout=_TIMEOUT)
    if resp.status_code == 404:  # unknown CID: not a transient failure
        return None
    resp.raise_for_status()  # 5xx / other -> retry
    return resp


def _extract_properties(resp: requests.Response | None) -> list[dict]:
    if resp is None:
        return []
    return resp.json().get("PropertyTable", {}).get("Properties", [])


@lru_cache(maxsize=2048)
def fetch_compound(cid: int) -> dict | None:
    """Return PubChem properties for a CID, or None if missing or the request fails."""
    url = f"{_BASE}/compound/cid/{cid}/property/{_PROPERTIES}/JSON"
    try:
        resp = _request(url)
    except requests.RequestException as exc:
        logger.warning("PubChem lookup failed for CID %s: %s", cid, exc)
        return None
    properties = _extract_properties(resp)
    return properties[0] if properties else None


def fetch_compounds(cids: Iterable[int]) -> dict[int, dict]:
    """Batch-resolve CIDs in a single request, keyed by CID.

    Only resolvable CIDs appear in the result. Falls back to per-CID lookups if the
    batch request fails as a whole (a single invalid CID can fault the batch), which
    also lets the per-CID cache absorb the valid ones.

    Note: uses a GET query, fine for the handful of active principles in a
    medication. For large batches PubChem recommends POST.
    """
    unique = sorted({int(c) for c in cids})
    if not unique:
        return {}
    joined = ",".join(str(c) for c in unique)
    url = f"{_BASE}/compound/cid/{joined}/property/{_PROPERTIES}/JSON"
    try:
        resp = _request(url)
    except requests.RequestException as exc:
        logger.warning("PubChem batch lookup failed for CIDs %s: %s", unique, exc)
        return _fetch_compounds_individually(unique)
    properties = _extract_properties(resp)
    if not properties and len(unique) > 1:
        return _fetch_compounds_individually(unique)
    return {p["CID"]: p for p in properties if "CID" in p}


def _fetch_compounds_individually(cids: Iterable[int]) -> dict[int, dict]:
    resolved = {cid: fetch_compound(cid) for cid in cids}
    return {cid: props for cid, props in resolved.items() if props}
