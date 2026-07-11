"""Tests for the deterministic severity maps in src/risk/severity.py."""

import pytest

from src.risk.severity import adverse_effect_severity, interaction_severity


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("minor", "low"),
        ("moderate", "moderate"),
        ("major", "high"),
        ("contraindicated", "critical"),
        (None, "moderate"),  # TWOSIDES ships severity unset -> default
    ],
)
def test_interaction_severity_map(native, expected):
    assert interaction_severity(native) == expected


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("severe", "high"),
        ("moderate", None),  # only severe raises an alert
        ("mild", None),
        (None, None),
    ],
)
def test_adverse_effect_severity_map(native, expected):
    assert adverse_effect_severity(native) == expected
