"""Deterministic maps from source severity scales to ``RiskSeverity``.

Pure functions, no I/O. Two source scales feed alerts: drug-drug interactions
(``InteractionSeverity``) and adverse effects (``SeverityLevel``). Drug-disease
and age-modifier alerts already carry a ``RiskSeverity`` from their store, so no
mapping is needed for those axes.
"""

from src.data.schemas.types import InteractionSeverity, SeverityLevel
from src.risk.models import RiskSeverity

# TWOSIDES interactions ship with severity unset; treat an unknown-strength
# co-reporting signal as moderate rather than dropping or overstating it.
_DEFAULT_INTERACTION_SEVERITY: RiskSeverity = "moderate"

_INTERACTION_MAP: dict[InteractionSeverity, RiskSeverity] = {
    "minor": "low",
    "moderate": "moderate",
    "major": "high",
    "contraindicated": "critical",
}


def interaction_severity(value: InteractionSeverity | None) -> RiskSeverity:
    """Map an interaction's severity to a ``RiskSeverity`` (moderate if unset)."""
    if value is None:
        return _DEFAULT_INTERACTION_SEVERITY
    return _INTERACTION_MAP[value]


def adverse_effect_severity(value: SeverityLevel | None) -> RiskSeverity | None:
    """Map an adverse effect's severity to a ``RiskSeverity``, or None.

    Only ``severe`` effects raise an alert (``high``); ``mild``/``moderate``/
    unset return None so the clinician is not flooded with trivial effects.
    """
    return "high" if value == "severe" else None
