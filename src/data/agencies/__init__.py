"""National medicines-agency adapters (product/formulary layer).

Registry ``AGENCIES`` maps a country code to its adapter. CIMA/AEMPS (ES) is the
only one implemented for now; structural normalization across agencies is
deferred — every adapter maps its native response to the common ``Product``.
"""

from src.data.agencies.base import AgencyAdapter, Product
from src.data.agencies.cima import CimaAdapter

AGENCIES: dict[str, AgencyAdapter] = {"ES": CimaAdapter()}

__all__ = ["AgencyAdapter", "Product", "CimaAdapter", "AGENCIES"]
