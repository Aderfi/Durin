import json
import re
from datetime import date
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from src.data.schemas.base import DomainModel
from src.data.schemas.types import (
    FrequencyCategory,
    InteractionSeverity,
    InteractionType,
    MedDRACode,
    NonEmptyStr,
    PubChemCID,
    SeverityLevel,
    SourceName,
)
from src.locales.loader import t

_ATC_DATA_PATH = Path(__file__).parent.parent / "atc" / "atc_codes.json"
_ATC_LOOKUP: dict[str, str] = json.loads(_ATC_DATA_PATH.read_text())

_ATC_PATTERN = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")

# InChIKey: 14 (skeleton) - 10 (layers) - 1 (protonation), all uppercase.
_INCHIKEY_PATTERN = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

# ATC code length by hierarchical level (1 char L1 ... 7 chars L5).
_LEVEL_LENGTHS = {1: 1, 2: 3, 3: 4, 4: 5, 5: 7}


class ATCCode(DomainModel):
    code: str = Field(description="ATC code (levels 1-5), e.g. 'J01CA04'.")
    name: str | None = Field(
        default=None,
        validate_default=True,
        description="Official code name, resolved from the local catalog.",
    )

    @field_validator("code")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if not _ATC_PATTERN.match(v):
            raise ValueError(t("validation.invalid_atc_code", code=v))
        return v

    @field_validator("name", mode="after")
    @classmethod
    def resolve_name(cls, v, info):
        code = info.data.get("code")
        return _ATC_LOOKUP.get(code, v)

    @property
    def level(self) -> int:
        """Hierarchical level of the ATC code (0 if the length is invalid)."""
        length_to_level = {length: level for level, length in _LEVEL_LENGTHS.items()}
        return length_to_level.get(len(self.code), 0)

    def get_parent_code(self, level: int) -> str | None:
        """Ancestor code at the given level, or None if not applicable."""
        target_length = _LEVEL_LENGTHS.get(level)
        if target_length is None or len(self.code) < target_length:
            return None
        return self.code[:target_length]

    def _group_name(self, level: int) -> str | None:
        parent = self.get_parent_code(level)
        return _ATC_LOOKUP.get(parent) if parent else None

    @property
    def anatomical_group(self) -> str | None:
        """Name of the main anatomical group (level 1)."""
        return self._group_name(1)

    @property
    def therapeutic_group(self) -> str | None:
        """Name of the therapeutic subgroup (level 2)."""
        return self._group_name(2)

    @property
    def pharmacological_class(self) -> str | None:
        """Name of the pharmacological subgroup (level 3)."""
        return self._group_name(3)

    @property
    def chemical_subgroup(self) -> str | None:
        """Name of the chemical subgroup (level 4)."""
        return self._group_name(4)

    @property
    def is_substance(self) -> bool:
        """True if the code identifies a substance (level 5)."""
        return self.level == 5


class Provenance(DomainModel):
    """Traceability for a single clinical fact (side effect or interaction).

    Every fact the risk engine consumes must name its source. `source_id` holds
    the native identifier (STITCH id, ChEMBL molregno, openFDA set_id); for
    ``source="LLM_NORMALIZED"`` it holds the original free text that was coded.
    """

    source: SourceName = Field(description="Where the datum comes from.")
    source_id: str | None = Field(
        default=None,
        description="Native source id, or original text for LLM_NORMALIZED.",
    )
    retrieved: date | None = Field(
        default=None, description="Extraction date (ETL run or Tier 2 cache write)."
    )


class SideEffect(DomainModel):
    name: NonEmptyStr = Field(description="Name of the adverse effect.")
    description: str | None = Field(
        default=None, description="Optional clinical description."
    )
    meddra_pt: NonEmptyStr | None = Field(
        default=None, description="MedDRA Preferred Term, if coded."
    )
    meddra_code: MedDRACode | None = Field(
        default=None, description="MedDRA numeric code, if coded."
    )
    severity: SeverityLevel | None = Field(
        default=None,
        description="Severity: mild | moderate | severe. None if no source signal.",
    )
    severity_derived: bool = Field(
        default=False,
        description="True if severity was inferred (not stated by the source).",
    )
    frequency: FrequencyCategory | None = Field(
        default=None, description="Population frequency of the effect, if known."
    )
    provenance: Provenance = Field(description="Source of this fact (required).")


class Interaction(DomainModel):
    interacting_drug_id: ATCCode | None = Field(
        default=None, description="ATC code of the interacting drug."
    )
    interacting_drug: NonEmptyStr | None = Field(
        default=None, description="Name of the interacting drug."
    )
    interaction_type: InteractionType | None = Field(
        default=None, description="Type: PD (pharmacodynamic) | PK (pharmacokinetic)."
    )
    severity: InteractionSeverity | None = Field(
        default=None,
        description="Severity: minor | moderate | major | contraindicated.",
    )
    mechanism: str | None = Field(
        default=None, description="Mechanism, e.g. 'CYP3A4 inhibition'."
    )
    description: str | None = Field(default=None, description="Free-text description.")
    management: str | None = Field(
        default=None, description="Management, e.g. 'avoid combination'."
    )
    provenance: Provenance = Field(description="Source of this fact (required).")

    @model_validator(mode="after")
    def require_drug_identity(self):
        if self.interacting_drug is None and self.interacting_drug_id is None:
            raise ValueError(t("validation.interaction_missing_drug"))
        return self


class Drug(DomainModel):
    cid: PubChemCID = Field(
        description="PubChem Compound ID; chemical identity of the active principle."
    )
    name: NonEmptyStr = Field(description="Compound name.")
    chemical_group: ATCCode | None = Field(
        default=None,
        description="ATC classification, if known (not provided by PubChem).",
    )
    molecular_formula: NonEmptyStr | None = Field(
        default=None, description="Molecular formula."
    )
    smiles: NonEmptyStr | None = Field(default=None, description="SMILES notation.")
    inchikey: str | None = Field(
        default=None, description="InChIKey (standard structural hash)."
    )
    side_effects: list[SideEffect] = Field(
        default_factory=list, description="Known adverse effects."
    )
    interactions: list[Interaction] = Field(
        default_factory=list, description="Known interactions."
    )

    @field_validator("inchikey")
    @classmethod
    def validate_inchikey(cls, v: str | None) -> str | None:
        if v is not None and not _INCHIKEY_PATTERN.match(v):
            raise ValueError(t("validation.invalid_inchikey", value=v))
        return v

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Drug) and self.cid == other.cid

    def __hash__(self) -> int:
        return hash(self.cid)

    @property
    def inchikey_skeleton(self) -> str | None:
        """First 14 InChIKey chars (connectivity block); groups stereoisomers."""
        return self.inchikey[:14] if self.inchikey else None

    @property
    def has_atc(self) -> bool:
        """True if the drug has an assigned ATC classification."""
        return self.chemical_group is not None
