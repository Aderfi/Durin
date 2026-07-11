"""National medicines-agency adapters (product/formulary layer).

Registry ``AGENCIES`` maps a country code to its adapter. CIMA/AEMPS (ES) is the
only one implemented for now; structural normalization across agencies is
deferred — every adapter maps its native response to the common ``Product``.

Note: the ``CimaAdapter`` and the ``AGENCIES`` registry are wired in a later
task; for now only the base contract is exported.
"""

from src.data.agencies.base import AgencyAdapter, Product

__all__ = ["AgencyAdapter", "Product"]
