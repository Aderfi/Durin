"""CIMA/AEMPS adapter — Spanish national medicines catalog.

Consumes the CIMA REST API (https://cima.aemps.es/cima/rest). I/O with retry;
maps CIMA's native JSON to the common ``Product``. Structural quirks of CIMA are
absorbed here so the rest of the system sees only ``Product``.
"""

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.data.agencies.base import Product
from src.data.schemas.drug import ATCCode
from src.utils.logging import get_logger

logger = get_logger(__name__)

_CIMA_MEDICAMENTOS = "https://cima.aemps.es/cima/rest/medicamentos"
_TIMEOUT = 15  # seconds


class CimaAdapter:
    """AgencyAdapter implementation for CIMA/AEMPS (country code 'ES')."""

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def _get(self, params: dict) -> dict:
        resp = requests.get(_CIMA_MEDICAMENTOS, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def lookup_product(self, query: str) -> list[Product]:
        """Search CIMA for products whose name matches ``query``."""
        try:
            payload = self._get({"nombre": query})
        except requests.RequestException:
            logger.error("CIMA lookup failed for query %r", query)
            return []
        return [self._to_product(item) for item in payload.get("resultados", [])]

    def get_active_principles(self, product: Product) -> list[Product]:
        """CIMA already returns active principles in the product record."""
        return [product]

    @staticmethod
    def _to_product(item: dict) -> Product:
        vtm = item.get("vtm") or {}
        atcs = item.get("atcs") or []
        atc = ATCCode(code=atcs[0]["codigo"]) if atcs else None
        principle = vtm.get("nombre")
        return Product(
            national_code=str(item["nregistro"]),
            name=item["nombre"],
            atc=atc,
            active_principle_names=[principle] if principle else [],
        )
