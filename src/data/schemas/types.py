"""Type aliases and 'enums' shared across the domain models.

Centralized here to avoid duplication and keep a single source of truth.
Categories are modeled as `Literal` (validated by Pydantic) instead of `enum.Enum`.
"""

from typing import Annotated, Literal

from pydantic import Field

type PositiveInt = Annotated[int, Field(gt=0)]
type NonEmptyStr = Annotated[str, Field(min_length=1)]

type PubChemCID = Annotated[int, Field(gt=0, le=10**9)] # PubChem Compound ID:

# Domain categories
FrequencyCategory = Literal[
    "very common",   # ≥1/10
    "common",        # ≥1/100 to <1/10
    "uncommon",      # ≥1/1,000 to <1/100
    "rare",          # ≥1/10,000 to <1/1,000
    "very rare",     # <1/10,000
]

SeverityLevel = Literal["mild", "moderate", "severe"]

InteractionType = Literal["PD", "PK"]
InteractionSeverity = Literal["minor", "moderate", "major", "contraindicated"]

# Provenance source names. LLM_NORMALIZED tags MedDRA codes derived by the
# offline term-normalizer (never a clinical assertion, only a coding step).
# BEERS tags age-modifier rules from the AGS Beers Criteria.
SourceName = Literal[
    "SIDER", "ChEMBL", "TWOSIDES", "openFDA", "CIMA", "LLM_NORMALIZED", "BEERS"
]

# MedDRA numeric code (Preferred/Lower Level Term id), 5 to 8 digits.
type MedDRACode = Annotated[str, Field(pattern=r"^\d{5,8}$")]
