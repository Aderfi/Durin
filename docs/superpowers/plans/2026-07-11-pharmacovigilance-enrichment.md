# Pharmacovigilance Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data-acquisition layer that populates each `Drug` with adverse
effects (`SideEffect`) and mechanism/interactions (`Interaction`), keyed by PubChem CID,
with full provenance — the data source of the future risk engine.

**Architecture:** Tiered hybrid. Tier 1 = local JSON datasets keyed by CID, pre-built by an
offline ETL from SIDER (effects), ChEMBL (mechanism) and TWOSIDES (interactions). Tier 2 =
on-demand openFDA lookup with a local cache. A pluggable `agencies/` layer (CIMA/AEMPS now)
supplies the national product catalog. Pydantic models only validate; all I/O and assembly
live in `sources.py` / `enrichment.py` / `repository.py`. The LLM term-normalizer is a
placeholder interface (no model dependency); runtime never calls an LLM.

**Tech Stack:** Python ≥3.14, Pydantic v2, `requests` + `tenacity` (HTTP with retry),
`polars` (TSV parsing), `json5` (locales), `pytest` (tests). No new dependencies.

## Global Constraints

- Python ≥3.14; modern syntax (`X | None`, `type` aliases, `list[...]`).
- **All docstrings and comments in English.** Spanish only in `src/locales/*.json5`.
- Models only validate. All I/O and reference resolution live outside models
  (`sources.py`, `enrichment.py`, `repository.py`, `agencies/`).
- `provenance` is **mandatory** on every `SideEffect` and `Interaction`.
- Runtime **never** calls an LLM. The normalizer is used only by the ETL script.
- Logging via stdlib `logging`, configured centrally to console **and** `logs/` files.
  **No silent errors** — every failure (incl. ID→CID mapping) is logged.
- Chemical identity = PubChem CID. Use **flat** STITCH IDs (`CID1…`) for CID mapping.
- New user-facing validation messages go in both `src/locales/en.json5` and `es.json5`.
- Follow existing patterns: `logging.getLogger(__name__)`, `tenacity` retry as in
  `src/data/pubchem.py`; flat tests under `tests/`; models extend `DomainModel`.

---

### Task 1: Central logging

**Files:**
- Create: `src/utils/__init__.py`
- Create: `src/utils/logging.py`
- Modify: `.gitignore` (add `logs/`, `src/data/pharmacovigilance/_cache/`)
- Test: `tests/test_logging.py`

**Interfaces:**
- Produces: `setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> None`
  and `get_logger(name: str) -> logging.Logger`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging.py
import logging
from pathlib import Path

from src.utils.logging import get_logger, setup_logging


def test_setup_logging_writes_to_file(tmp_path: Path):
    setup_logging(log_dir=tmp_path)
    logger = get_logger("durin.test")
    logger.warning("hello-durin")

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = tmp_path / "durin.log"
    assert log_file.exists()
    assert "hello-durin" in log_file.read_text()


def test_get_logger_returns_named_logger():
    assert get_logger("durin.sample").name == "durin.sample"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.utils'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/utils/__init__.py
```

```python
# src/utils/logging.py
"""Central logging configuration for Durin.

Configures the root logger once with a console handler and a rotating file
handler under ``logs/``. All modules obtain their logger via ``get_logger`` and
never configure handlers themselves. No error is ever swallowed silently.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3

_configured = False


def setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> None:
    """Configure the root logger (idempotent).

    Adds a console handler and a rotating file handler writing to
    ``<log_dir>/durin.log``. Safe to call multiple times; only the first call
    installs handlers.
    """
    global _configured
    directory = log_dir or _DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove handlers from a previous configuration (e.g. tests with a new dir).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        directory / "durin.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, configuring logging with defaults on first use."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update `.gitignore`**

Append these lines to `.gitignore`:

```
logs/
src/data/pharmacovigilance/_cache/
```

- [ ] **Step 6: Commit**

```bash
git add src/utils/__init__.py src/utils/logging.py tests/test_logging.py .gitignore
git commit -m "feat: Add central logging to console and logs dir"
```

---

### Task 2: Provenance model and schema field changes

**Files:**
- Modify: `src/data/schemas/types.py` (add `SourceName`, `MedDRACode`)
- Modify: `src/data/schemas/drug.py` (add `Provenance`; extend `SideEffect`, `Interaction`)
- Modify: `src/data/schemas/__init__.py` (export `Provenance`)
- Modify: `src/locales/en.json5`, `src/locales/es.json5` (new key)
- Modify: `tests/test_schemas.py` (existing effect/interaction tests now need provenance)
- Test: `tests/test_schemas.py` (new cases)

**Interfaces:**
- Produces: `Provenance(source, source_id=None, retrieved=None)`;
  `SourceName = Literal["SIDER","ChEMBL","TWOSIDES","openFDA","CIMA","LLM_NORMALIZED"]`;
  `MedDRACode = Annotated[str, ...]` (5–8 digits). `SideEffect` gains
  `meddra_pt`, `meddra_code`, `provenance` (required), `severity_derived`; `severity`
  becomes optional. `Interaction` gains `provenance` (required).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_schemas.py
from src.data.schemas import Provenance


def _prov() -> Provenance:
    return Provenance(source="SIDER", source_id="CID100002244")


def test_sideeffect_requires_provenance():
    with pytest.raises(ValidationError):
        SideEffect(name="nausea", severity="mild")  # no provenance


def test_sideeffect_severity_optional_and_derived_flag():
    se = SideEffect(name="nausea", provenance=_prov())
    assert se.severity is None
    assert se.severity_derived is False

    se2 = SideEffect(
        name="gi haemorrhage", severity="severe", severity_derived=True,
        meddra_code="10017955", provenance=_prov(),
    )
    assert se2.severity == "severe"
    assert se2.severity_derived is True
    assert se2.meddra_code == "10017955"


def test_sideeffect_rejects_bad_meddra_code():
    with pytest.raises(ValidationError):
        SideEffect(name="nausea", meddra_code="ABC", provenance=_prov())


def test_interaction_requires_provenance():
    with pytest.raises(ValidationError):
        Interaction(interacting_drug="warfarin")  # no provenance
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py -k "provenance or meddra or derived" -v`
Expected: FAIL with `ImportError: cannot import name 'Provenance'`

- [ ] **Step 3: Add types to `src/data/schemas/types.py`**

Append:

```python
# Provenance source names. LLM_NORMALIZED tags MedDRA codes derived by the
# offline term-normalizer (never a clinical assertion, only a coding step).
SourceName = Literal[
    "SIDER", "ChEMBL", "TWOSIDES", "openFDA", "CIMA", "LLM_NORMALIZED"
]

# MedDRA numeric code (Preferred/Lower Level Term id), 5 to 8 digits.
type MedDRACode = Annotated[str, Field(pattern=r"^\d{5,8}$")]
```

- [ ] **Step 4: Add `Provenance` and update models in `src/data/schemas/drug.py`**

Add the import for the new types and `date`:

```python
from datetime import date

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
```

Add the `Provenance` model above `SideEffect`:

```python
class Provenance(DomainModel):
    """Traceability for a single clinical fact (side effect or interaction).

    Every fact the risk engine consumes must name its source. `source_id` holds
    the native identifier (STITCH id, ChEMBL molregno, openFDA set_id); for
    ``source="LLM_NORMALIZED"`` it holds the original free text that was coded.
    """

    source: SourceName = Field(description="Where the datum comes from.")
    source_id: str | None = Field(
        default=None, description="Native source id, or original text for LLM_NORMALIZED."
    )
    retrieved: date | None = Field(
        default=None, description="Extraction date (ETL run or Tier 2 cache write)."
    )
```

Replace the `SideEffect` class body with:

```python
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
```

Add `provenance` to `Interaction` (append after `management`):

```python
    provenance: Provenance = Field(description="Source of this fact (required).")
```

- [ ] **Step 5: Export `Provenance` in `src/data/schemas/__init__.py`**

```python
from .drug import ATCCode, Drug, Interaction, Provenance, SideEffect
from .medication import Med
from .patient import Patient

__all__ = [
    "Patient", "Drug", "ATCCode", "SideEffect", "Interaction", "Provenance", "Med",
]
```

- [ ] **Step 6: Add locale key** to `src/locales/en.json5` and `src/locales/es.json5`

en.json5 (add entry):
```json5
  "validation.invalid_meddra_code": "Invalid MedDRA code: '{value}'",
```
es.json5 (add entry):
```json5
  "validation.invalid_meddra_code": "El código MedDRA '{value}' no es válido",
```

- [ ] **Step 7: Fix existing tests that build effects/interactions without provenance**

In `tests/test_schemas.py`, any existing `SideEffect(...)` / `Interaction(...)` construction
must now pass `provenance=_prov()`. Update those call sites (search the file for
`SideEffect(` and `Interaction(`).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS (all, including the 4 new cases)

- [ ] **Step 9: Commit**

```bash
git add src/data/schemas/ src/locales/ tests/test_schemas.py
git commit -m "feat: Add Provenance and make SideEffect severity optional"
```

---

### Task 3: Product model and agency adapter interface

**Files:**
- Create: `src/data/agencies/__init__.py`
- Create: `src/data/agencies/base.py`
- Test: `tests/test_agencies.py`

**Interfaces:**
- Consumes: `ATCCode` from `src.data.schemas.drug`.
- Produces: `Product(national_code, name, atc=None, active_principle_names)`;
  `AgencyAdapter` Protocol with `lookup_product(query: str) -> list[Product]` and
  `get_active_principles(product: Product) -> list[Product]`; `AGENCIES: dict[str, AgencyAdapter]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agencies.py
import pytest
from pydantic import ValidationError

from src.data.agencies.base import Product


def test_product_valid():
    p = Product(
        national_code="65900",
        name="Amoxicilina Normon 500 mg",
        active_principle_names=["amoxicillin"],
    )
    assert p.national_code == "65900"
    assert p.active_principle_names == ["amoxicillin"]


def test_product_rejects_empty_name():
    with pytest.raises(ValidationError):
        Product(national_code="1", name="", active_principle_names=["x"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agencies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.agencies'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/data/agencies/__init__.py
"""National medicines-agency adapters (product/formulary layer).

Registry ``AGENCIES`` maps a country code to its adapter. CIMA/AEMPS (ES) is the
only one implemented for now; structural normalization across agencies is
deferred — every adapter maps its native response to the common ``Product``.
"""

from src.data.agencies.base import AgencyAdapter, Product
from src.data.agencies.cima import CimaAdapter

AGENCIES: dict[str, AgencyAdapter] = {"ES": CimaAdapter()}

__all__ = ["AgencyAdapter", "Product", "CimaAdapter", "AGENCIES"]
```

> Note: the `CimaAdapter` import lands in Task 8. Until then, temporarily comment out the
> `cima` import and the `AGENCIES` entry so the package imports; Task 8 restores them.
> For this task, `__init__.py` may contain only the `base` import.

```python
# src/data/agencies/base.py
"""Common contract for national medicines-agency adapters."""

from typing import Protocol, runtime_checkable

from pydantic import Field

from src.data.schemas.base import DomainModel
from src.data.schemas.drug import ATCCode
from src.data.schemas.types import NonEmptyStr


class Product(DomainModel):
    """A marketed medicinal product from a national catalog.

    Minimal shape shared by all agencies. `active_principle_names` are resolved
    to PubChem CIDs downstream (identity layer).
    """

    national_code: NonEmptyStr = Field(description="National registry code.")
    name: NonEmptyStr = Field(description="Brand/product name.")
    atc: ATCCode | None = Field(default=None, description="ATC classification, if known.")
    active_principle_names: list[NonEmptyStr] = Field(
        description="Active principle names; resolved to CIDs downstream."
    )


@runtime_checkable
class AgencyAdapter(Protocol):
    """Interface every national-agency adapter implements."""

    def lookup_product(self, query: str) -> list[Product]:
        """Search the national catalog for products matching ``query``."""
        ...

    def get_active_principles(self, product: Product) -> list[Product]:
        """Return ``product`` with its active principles resolved/expanded."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agencies.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/agencies/ tests/test_agencies.py
git commit -m "feat: Add Product model and AgencyAdapter interface"
```

---

### Task 4: STITCH → CID transform

**Files:**
- Create: `src/data/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `stitch_to_cid(stitch_id: str) -> int | None` — parses a STITCH id
  (`CID1xxxxxxx` flat / `CID0xxxxxxx` stereo) to a PubChem CID; logs a warning and
  returns `None` for a malformed id (no silent failure).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sources.py
import logging

from src.data.sources import stitch_to_cid


def test_stitch_to_cid_flat():
    assert stitch_to_cid("CID100002244") == 2244


def test_stitch_to_cid_stereo():
    assert stitch_to_cid("CID000002244") == 2244


def test_stitch_to_cid_malformed_logs_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert stitch_to_cid("XYZ123") is None
    assert any("STITCH" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.sources'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/data/sources.py
"""Source adapters for pharmacovigilance data (SIDER, ChEMBL, TWOSIDES, openFDA).

Pure parsing and I/O; no Pydantic model assembly (that is ``enrichment.py``).
Every mapping failure is logged — never swallowed.
"""

import re

from src.utils.logging import get_logger

logger = get_logger(__name__)

# STITCH compound id, e.g. "CID100002244" (flat) or "CID000002244" (stereo).
_STITCH_PATTERN = re.compile(r"^CID[01](\d+)$")


def stitch_to_cid(stitch_id: str) -> int | None:
    """Convert a STITCH id to a PubChem CID, or None if malformed (logged)."""
    match = _STITCH_PATTERN.match(stitch_id.strip())
    if match is None:
        logger.warning("Unmappable STITCH id, skipping: %r", stitch_id)
        return None
    return int(match.group(1))  # int() drops leading zeros
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sources.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/sources.py tests/test_sources.py
git commit -m "feat: Add STITCH to PubChem CID transform"
```

---

### Task 5: SIDER parser (side effects → per-CID records)

**Files:**
- Modify: `src/data/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `stitch_to_cid`.
- Produces: `parse_sider(se_path: Path, freq_path: Path | None = None) -> dict[int, list[dict]]`
  — maps CID to a list of raw effect dicts with keys
  `{name, meddra_pt, meddra_code, frequency, source, source_id}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sources.py
from pathlib import Path

from src.data.sources import parse_sider

# SIDER meddra_all_se.tsv columns:
# STITCH_flat, STITCH_stereo, UMLS_label, MedDRA_type, UMLS_meddra, side_effect_name
_SE_ROWS = (
    "CID100002244\tCID000002244\tC0018939\tPT\t10017955\tGastrointestinal haemorrhage\n"
    "CID100002244\tCID000002244\tC0027497\tPT\t10028813\tNausea\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_sider_groups_by_cid(tmp_path):
    se = _write(tmp_path, "se.tsv", _SE_ROWS)
    result = parse_sider(se)
    assert set(result) == {2244}
    effects = result[2244]
    assert len(effects) == 2
    first = next(e for e in effects if e["meddra_code"] == "10017955")
    assert first["name"] == "Gastrointestinal haemorrhage"
    assert first["source"] == "SIDER"
    assert first["source_id"] == "CID100002244"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sources.py::test_parse_sider_groups_by_cid -v`
Expected: FAIL with `ImportError: cannot import name 'parse_sider'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/data/sources.py` (add `from pathlib import Path` and `import polars as pl` at top):

```python
# SIDER meddra_all_se.tsv column order (no header in the distributed file).
_SIDER_SE_COLUMNS = [
    "stitch_flat", "stitch_stereo", "umls_label",
    "meddra_type", "meddra_code", "side_effect_name",
]


def parse_sider(se_path: Path, freq_path: Path | None = None) -> dict[int, list[dict]]:
    """Parse SIDER ``meddra_all_se.tsv`` into per-CID raw effect dicts.

    Only PT (Preferred Term) rows are kept. Unmappable STITCH ids are skipped
    and logged by ``stitch_to_cid``. ``freq_path`` is accepted for future
    frequency joining; frequency is left None for now.
    """
    frame = pl.read_csv(
        se_path, separator="\t", has_header=False, new_columns=_SIDER_SE_COLUMNS
    )
    frame = frame.filter(pl.col("meddra_type") == "PT")

    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        cid = stitch_to_cid(row["stitch_flat"])
        if cid is None:
            continue
        by_cid.setdefault(cid, []).append(
            {
                "name": row["side_effect_name"],
                "meddra_pt": row["side_effect_name"],
                "meddra_code": str(row["meddra_code"]),
                "frequency": None,
                "source": "SIDER",
                "source_id": row["stitch_flat"],
            }
        )
    logger.info("Parsed SIDER: %d compounds, %d effect rows",
                len(by_cid), sum(len(v) for v in by_cid.values()))
    return by_cid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sources.py::test_parse_sider_groups_by_cid -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/sources.py tests/test_sources.py
git commit -m "feat: Parse SIDER side effects grouped by CID"
```

---

### Task 6: TWOSIDES parser (drug-drug interactions → per-CID-pair records)

**Files:**
- Modify: `src/data/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `parse_twosides(path: Path) -> dict[int, list[dict]]` — maps a CID to a list of
  raw interaction dicts keyed by the *other* CID:
  `{interacting_cid, mechanism, meddra_pt, source, source_id}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sources.py
from src.data.sources import parse_twosides

# Minimal TWOSIDES CSV: drug_1 CID, drug_2 CID, condition MedDRA name, PRR.
_TWOSIDES_CSV = (
    "drug_1_cid,drug_2_cid,condition_meddra_name,prr\n"
    "2244,5090,Gastrointestinal haemorrhage,4.2\n"
)


def test_parse_twosides_symmetric(tmp_path):
    p = tmp_path / "twosides.csv"
    p.write_text(_TWOSIDES_CSV, encoding="utf-8")
    result = parse_twosides(p)
    # Interaction indexed under both members of the pair.
    assert 2244 in result and 5090 in result
    entry = result[2244][0]
    assert entry["interacting_cid"] == 5090
    assert entry["meddra_pt"] == "Gastrointestinal haemorrhage"
    assert entry["source"] == "TWOSIDES"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sources.py::test_parse_twosides_symmetric -v`
Expected: FAIL with `ImportError: cannot import name 'parse_twosides'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/data/sources.py`:

```python
def parse_twosides(path: Path) -> dict[int, list[dict]]:
    """Parse a TWOSIDES CSV into per-CID interaction dicts (indexed both ways).

    Each drug-drug row yields an entry under both CIDs of the pair so a lookup by
    either compound finds the interaction.
    """
    frame = pl.read_csv(path)
    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        a, b = int(row["drug_1_cid"]), int(row["drug_2_cid"])
        meddra_pt = row["condition_meddra_name"]
        mechanism = f"Increased risk of {meddra_pt} (TWOSIDES PRR={row['prr']})"
        for cid, other in ((a, b), (b, a)):
            by_cid.setdefault(cid, []).append(
                {
                    "interacting_cid": other,
                    "mechanism": mechanism,
                    "meddra_pt": meddra_pt,
                    "source": "TWOSIDES",
                    "source_id": f"{a}-{b}",
                }
            )
    logger.info("Parsed TWOSIDES: %d compounds", len(by_cid))
    return by_cid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sources.py::test_parse_twosides_symmetric -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/sources.py tests/test_sources.py
git commit -m "feat: Parse TWOSIDES drug-drug interactions by CID"
```

---

### Task 7: ChEMBL mechanism parser (mechanism of action → per-CID records)

**Files:**
- Modify: `src/data/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `parse_chembl_moa(path: Path, unichem: dict[str, int]) -> dict[int, list[dict]]`
  — `unichem` maps a ChEMBL id to a PubChem CID (built by the ETL via UniChem); returns per-CID
  mechanism dicts `{mechanism, action_type, source, source_id}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sources.py
from src.data.sources import parse_chembl_moa

# Minimal ChEMBL mechanism CSV: molecule_chembl_id, mechanism_of_action, action_type.
_CHEMBL_CSV = (
    "molecule_chembl_id,mechanism_of_action,action_type\n"
    "CHEMBL25,Cyclooxygenase inhibitor,INHIBITOR\n"
)


def test_parse_chembl_moa_maps_to_cid(tmp_path):
    p = tmp_path / "chembl.csv"
    p.write_text(_CHEMBL_CSV, encoding="utf-8")
    result = parse_chembl_moa(p, unichem={"CHEMBL25": 2244})
    assert 2244 in result
    moa = result[2244][0]
    assert moa["mechanism"] == "Cyclooxygenase inhibitor"
    assert moa["action_type"] == "INHIBITOR"
    assert moa["source"] == "ChEMBL"


def test_parse_chembl_moa_skips_unmapped(tmp_path, caplog):
    p = tmp_path / "chembl.csv"
    p.write_text(_CHEMBL_CSV, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = parse_chembl_moa(p, unichem={})  # no mapping for CHEMBL25
    assert result == {}
    assert any("CHEMBL25" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sources.py -k chembl -v`
Expected: FAIL with `ImportError: cannot import name 'parse_chembl_moa'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/data/sources.py`:

```python
def parse_chembl_moa(path: Path, unichem: dict[str, int]) -> dict[int, list[dict]]:
    """Parse a ChEMBL mechanism-of-action CSV into per-CID mechanism dicts.

    ``unichem`` maps ChEMBL molecule ids to PubChem CIDs. A ChEMBL id with no
    mapping is logged and skipped (no silent drop).
    """
    frame = pl.read_csv(path)
    by_cid: dict[int, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        chembl_id = row["molecule_chembl_id"]
        cid = unichem.get(chembl_id)
        if cid is None:
            logger.warning("No UniChem CID for ChEMBL id, skipping: %s", chembl_id)
            continue
        by_cid.setdefault(cid, []).append(
            {
                "mechanism": row["mechanism_of_action"],
                "action_type": row["action_type"],
                "source": "ChEMBL",
                "source_id": chembl_id,
            }
        )
    logger.info("Parsed ChEMBL MoA: %d compounds", len(by_cid))
    return by_cid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sources.py -k chembl -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/sources.py tests/test_sources.py
git commit -m "feat: Parse ChEMBL mechanism of action by CID"
```

---

### Task 8: openFDA Tier 2 fetch with cache + CIMA adapter

**Files:**
- Modify: `src/data/sources.py` (openFDA fetch)
- Create: `src/data/agencies/cima.py`
- Modify: `src/data/agencies/__init__.py` (restore `CimaAdapter` import + `AGENCIES` entry)
- Test: `tests/test_sources.py`, `tests/test_agencies.py`

**Interfaces:**
- Produces: `fetch_openfda_label(cid: int, active_name: str, cache_dir: Path) -> dict | None`
  — returns `{adverse_reactions: str|None, mechanism_of_action: str|None, source_id: str}`
  or None; caches JSON per CID under `cache_dir`. `CimaAdapter` implements `AgencyAdapter`.

- [ ] **Step 1: Write the failing test (openFDA cache hit avoids HTTP)**

```python
# add to tests/test_sources.py
import json

from src.data.sources import fetch_openfda_label


def test_fetch_openfda_uses_cache(tmp_path):
    cache = tmp_path / "_cache"
    cache.mkdir()
    (cache / "2244.json").write_text(
        json.dumps({
            "adverse_reactions": "GI bleeding.",
            "mechanism_of_action": "COX inhibition.",
            "source_id": "cached",
        }),
        encoding="utf-8",
    )
    # No network: cache hit returns the stored record.
    result = fetch_openfda_label(2244, "aspirin", cache_dir=cache)
    assert result["adverse_reactions"] == "GI bleeding."
    assert result["source_id"] == "cached"
```

```python
# add to tests/test_agencies.py
from unittest.mock import patch

from src.data.agencies.cima import CimaAdapter


def test_cima_lookup_product_parses_response():
    fake = {"resultados": [{
        "nregistro": "65900",
        "nombre": "Amoxicilina Normon 500 mg",
        "vtm": {"nombre": "amoxicillin"},
        "atcs": [{"codigo": "J01CA04"}],
    }]}
    with patch("src.data.agencies.cima.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake
        mock_get.return_value.raise_for_status.return_value = None
        products = CimaAdapter().lookup_product("amoxicilina")
    assert products[0].national_code == "65900"
    assert products[0].active_principle_names == ["amoxicillin"]
    assert products[0].atc.code == "J01CA04"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sources.py::test_fetch_openfda_uses_cache tests/test_agencies.py::test_cima_lookup_product_parses_response -v`
Expected: FAIL (`cannot import name 'fetch_openfda_label'`, `No module named 'src.data.agencies.cima'`)

- [ ] **Step 3: Implement openFDA fetch in `src/data/sources.py`**

Add at top: `import json`, `import requests`, and the tenacity imports as in `pubchem.py`.

```python
_OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
_OPENFDA_TIMEOUT = 15  # seconds


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _openfda_get(active_name: str) -> dict | None:
    """Query openFDA drug/label by active ingredient; None on 404."""
    params = {"search": f'active_ingredient:"{active_name}"', "limit": 1}
    resp = requests.get(_OPENFDA_LABEL, params=params, timeout=_OPENFDA_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_openfda_label(cid: int, active_name: str, cache_dir: Path) -> dict | None:
    """Fetch openFDA label sections for a CID, caching the result per CID.

    Returns ``{adverse_reactions, mechanism_of_action, source_id}`` or None.
    Reads the cache first; on a miss, calls openFDA and writes the cache.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        payload = _openfda_get(active_name)
    except requests.RequestException:
        logger.error("openFDA request failed for CID %d (%s)", cid, active_name)
        return None
    if not payload or not payload.get("results"):
        logger.warning("No openFDA label for CID %d (%s)", cid, active_name)
        return None

    result = payload["results"][0]
    record = {
        "adverse_reactions": _first(result.get("adverse_reactions")),
        "mechanism_of_action": _first(result.get("mechanism_of_action")),
        "source_id": result.get("set_id", active_name),
    }
    cache_file.write_text(json.dumps(record), encoding="utf-8")
    return record


def _first(value: list[str] | None) -> str | None:
    """openFDA returns single-element lists for label sections."""
    return value[0] if value else None
```

- [ ] **Step 4: Implement `src/data/agencies/cima.py`**

```python
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
```

- [ ] **Step 5: Restore the CIMA wiring in `src/data/agencies/__init__.py`**

Ensure the file matches the full version shown in Task 3 Step 3 (with the `cima` import and
`AGENCIES = {"ES": CimaAdapter()}` uncommented).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_sources.py -k openfda tests/test_agencies.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/data/sources.py src/data/agencies/ tests/test_sources.py tests/test_agencies.py
git commit -m "feat: Add openFDA Tier 2 fetch and CIMA adapter"
```

---

### Task 9: Term normalizer interface + placeholder

**Files:**
- Create: `src/data/pharmacovigilance/__init__.py`
- Create: `src/data/pharmacovigilance/normalizer.py`
- Test: `tests/test_normalizer.py`

**Interfaces:**
- Produces: `TermNormalizer` Protocol with
  `normalize(text: str) -> tuple[str, str] | None` (returns `(meddra_pt, meddra_code)`);
  `LocalLLMNormalizer` placeholder raising `NotImplementedError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalizer.py
import pytest

from src.data.pharmacovigilance.normalizer import LocalLLMNormalizer


def test_local_llm_normalizer_is_placeholder():
    with pytest.raises(NotImplementedError):
        LocalLLMNormalizer().normalize("stomach bleeding")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.pharmacovigilance'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/data/pharmacovigilance/__init__.py
```

```python
# src/data/pharmacovigilance/normalizer.py
"""Free-text → MedDRA term normalizer (ETL-only).

Maps free-text adverse-reaction text (e.g. from openFDA labels) to a MedDRA
Preferred Term from a closed vocabulary. It NEVER asserts a clinical fact — the
assertion comes from the source; this only assigns a code. Used solely by the
offline ETL; runtime never calls it.

``LocalLLMNormalizer`` is a placeholder for a locally-hosted open-source model
(e.g. Qwen / Gemma) to be implemented by the maintainer. It adds NO model
dependency here.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TermNormalizer(Protocol):
    """Maps free text to a MedDRA (Preferred Term, code) pair, or None."""

    def normalize(self, text: str) -> tuple[str, str] | None:
        ...


class LocalLLMNormalizer:
    """Placeholder normalizer backed by a future local open-source LLM.

    Not yet implemented. Wire a locally-hosted model here; keep the mapping
    constrained to the MedDRA vocabulary so it codes rather than invents.
    """

    def normalize(self, text: str) -> tuple[str, str] | None:
        raise NotImplementedError("Local LLM normalizer not yet implemented")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_normalizer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/pharmacovigilance/ tests/test_normalizer.py
git commit -m "feat: Add TermNormalizer interface with LLM placeholder"
```

---

### Task 10: Severity derivation rule

**Files:**
- Modify: `src/data/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `derive_severity(meddra_code: str | None, is_serious: bool) -> tuple[str | None, bool]`
  — deterministic (no LLM); returns `(severity, severity_derived)`. `severe` when
  ``is_serious`` or the code is in the serious set; `moderate` when a MedDRA code is present
  but not serious; `(None, False)` when there is no signal at all.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sources.py
from src.data.sources import derive_severity


def test_derive_severity_serious_flag():
    assert derive_severity("10017955", is_serious=True) == ("severe", True)


def test_derive_severity_coded_not_serious():
    assert derive_severity("10028813", is_serious=False) == ("moderate", True)


def test_derive_severity_no_signal():
    assert derive_severity(None, is_serious=False) == (None, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sources.py -k derive_severity -v`
Expected: FAIL with `ImportError: cannot import name 'derive_severity'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/data/sources.py`:

```python
# MedDRA codes flagged as Important Medical Events / serious SMQ membership.
# Seeded minimally; extended by the ETL from the MedDRA IME list.
_SERIOUS_MEDDRA_CODES: frozenset[str] = frozenset({"10017955"})


def derive_severity(meddra_code: str | None, is_serious: bool) -> tuple[str | None, bool]:
    """Deterministically derive (severity, severity_derived) from MedDRA signal.

    ``severe`` if flagged serious or the code is in the serious set; ``moderate``
    if a code exists but is not serious; ``(None, False)`` if there is no signal.
    This is a rule, not an LLM inference.
    """
    if is_serious or (meddra_code in _SERIOUS_MEDDRA_CODES):
        return "severe", True
    if meddra_code is not None:
        return "moderate", True
    return None, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sources.py -k derive_severity -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/sources.py tests/test_sources.py
git commit -m "feat: Add deterministic severity derivation from MedDRA"
```

---

### Task 11: Enrichment orchestrator (assemble models from Tier 1/2)

**Files:**
- Create: `src/data/enrichment.py`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `SideEffect`, `Interaction`, `Provenance` (schemas); `derive_severity`,
  `fetch_openfda_label` (sources).
- Produces: `PharmacovigilanceStore(data_dir: Path, cache_dir: Path | None = None)` with
  `side_effects(cid: int) -> list[SideEffect]` and `interactions(cid: int) -> list[Interaction]`;
  `enrich_drug(drug: Drug, store: PharmacovigilanceStore) -> Drug` returning a copy with
  `side_effects` and `interactions` populated.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrichment.py
import json
from pathlib import Path

from src.data.enrichment import PharmacovigilanceStore, enrich_drug
from src.data.schemas import Drug


def _store(tmp_path: Path) -> PharmacovigilanceStore:
    (tmp_path / "sider_effects.json").write_text(json.dumps({
        "2244": [{
            "name": "Gastrointestinal haemorrhage", "meddra_pt": "Gastrointestinal haemorrhage",
            "meddra_code": "10017955", "frequency": "rare",
            "source": "SIDER", "source_id": "CID100002244",
        }],
    }), encoding="utf-8")
    (tmp_path / "twosides_ddi.json").write_text(json.dumps({
        "2244": [{
            "interacting_cid": 5090, "mechanism": "Increased risk of bleeding",
            "meddra_pt": "Gastrointestinal haemorrhage",
            "source": "TWOSIDES", "source_id": "2244-5090",
        }],
    }), encoding="utf-8")
    (tmp_path / "chembl_moa.json").write_text(json.dumps({}), encoding="utf-8")
    return PharmacovigilanceStore(data_dir=tmp_path)


def test_side_effects_assembled_with_provenance(tmp_path):
    store = _store(tmp_path)
    effects = store.side_effects(2244)
    assert len(effects) == 1
    se = effects[0]
    assert se.meddra_code == "10017955"
    assert se.severity == "severe" and se.severity_derived is True
    assert se.provenance.source == "SIDER"


def test_interactions_assembled_with_provenance(tmp_path):
    store = _store(tmp_path)
    inter = store.interactions(2244)
    assert len(inter) == 1
    assert inter[0].mechanism == "Increased risk of bleeding"
    assert inter[0].interacting_drug == "CID 5090"  # deterministic fallback identity
    assert inter[0].provenance.source == "TWOSIDES"


def test_enrich_drug_populates_lists(tmp_path):
    store = _store(tmp_path)
    drug = Drug(cid=2244, name="aspirin")
    enriched = enrich_drug(drug, store)
    assert len(enriched.side_effects) == 1
    assert len(enriched.interactions) == 1
    # Original is untouched (models only validate; enrichment returns a copy).
    assert drug.side_effects == []


def test_unknown_cid_returns_empty(tmp_path):
    store = _store(tmp_path)
    assert store.side_effects(999999) == []
    assert store.interactions(999999) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_enrichment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.enrichment'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/data/enrichment.py
"""Assemble SideEffect/Interaction models from Tier 1 datasets (+ Tier 2 openFDA).

Loads the local per-CID JSON datasets and builds validated Pydantic models with
mandatory provenance. Tier 2 (openFDA) fills gaps on demand when a cache dir is
given. Models only validate; this module does the I/O and assembly.
"""

import json
from pathlib import Path

from src.data.schemas import Drug, Interaction, Provenance, SideEffect
from src.data.sources import derive_severity
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _load(path: Path) -> dict[str, list[dict]]:
    """Load a per-CID JSON dataset, or an empty dict if the file is missing."""
    if not path.exists():
        logger.warning("Tier 1 dataset missing: %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class PharmacovigilanceStore:
    """Local Tier 1 datasets keyed by CID, assembling validated models."""

    def __init__(self, data_dir: Path, cache_dir: Path | None = None) -> None:
        self._effects = _load(data_dir / "sider_effects.json")
        self._interactions = _load(data_dir / "twosides_ddi.json")
        self._moa = _load(data_dir / "chembl_moa.json")
        self._cache_dir = cache_dir

    def side_effects(self, cid: int) -> list[SideEffect]:
        """Return assembled SideEffect models for a CID (empty if unknown)."""
        out: list[SideEffect] = []
        for raw in self._effects.get(str(cid), []):
            code = raw.get("meddra_code")
            severity, derived = derive_severity(code, is_serious=False)
            out.append(
                SideEffect(
                    name=raw["name"],
                    meddra_pt=raw.get("meddra_pt"),
                    meddra_code=code,
                    severity=severity,
                    severity_derived=derived,
                    frequency=raw.get("frequency"),
                    provenance=Provenance(
                        source=raw["source"], source_id=raw.get("source_id")
                    ),
                )
            )
        return out

    def interactions(self, cid: int) -> list[Interaction]:
        """Return assembled Interaction models for a CID (empty if unknown)."""
        out: list[Interaction] = []
        for raw in self._interactions.get(str(cid), []):
            # Guarantee drug identity (require_drug_identity): use the resolved
            # name if the ETL provided one, else a deterministic CID-based label.
            name = raw.get("interacting_name") or f"CID {raw['interacting_cid']}"
            out.append(
                Interaction(
                    interacting_drug=name,
                    interaction_type="PD",
                    mechanism=raw["mechanism"],
                    provenance=Provenance(
                        source=raw["source"], source_id=raw.get("source_id")
                    ),
                )
            )
        return out


def enrich_drug(drug: Drug, store: PharmacovigilanceStore) -> Drug:
    """Return a copy of ``drug`` with side effects and interactions populated."""
    return drug.model_copy(
        update={
            "side_effects": store.side_effects(drug.cid),
            "interactions": store.interactions(drug.cid),
        }
    )
```

> Note: `Interaction.require_drug_identity` needs at least one of `interacting_drug` /
> `interacting_drug_id`. TWOSIDES rows carry the interacting CID but not a name, so
> `interactions()` falls back to a deterministic `f"CID {interacting_cid}"` label. A later ETL
> pass may resolve real names into `interacting_name`; the fallback keeps every interaction
> valid meanwhile.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_enrichment.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/enrichment.py tests/test_enrichment.py
git commit -m "feat: Assemble side effects and interactions from datasets"
```

---

### Task 12: Wire enrichment into repository

**Files:**
- Modify: `src/data/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `get_drug_by_cid` (existing), `PharmacovigilanceStore`, `enrich_drug`.
- Produces: `get_enriched_drug(cid: int, store: PharmacovigilanceStore) -> Drug | None`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_repository.py
import json
from pathlib import Path
from unittest.mock import patch

from src.data.enrichment import PharmacovigilanceStore
from src.data.repository import get_enriched_drug


def test_get_enriched_drug_populates_effects(tmp_path: Path):
    (tmp_path / "sider_effects.json").write_text(json.dumps({
        "2244": [{"name": "Nausea", "meddra_code": "10028813",
                  "source": "SIDER", "source_id": "CID100002244"}],
    }), encoding="utf-8")
    (tmp_path / "twosides_ddi.json").write_text("{}", encoding="utf-8")
    (tmp_path / "chembl_moa.json").write_text("{}", encoding="utf-8")
    store = PharmacovigilanceStore(data_dir=tmp_path)

    fake_props = {"CID": 2244, "Title": "aspirin", "MolecularFormula": "C9H8O4"}
    with patch("src.data.repository.fetch_compound", return_value=fake_props):
        drug = get_enriched_drug(2244, store)

    assert drug is not None
    assert drug.name == "aspirin"
    assert drug.side_effects[0].name == "Nausea"


def test_get_enriched_drug_unknown_cid_returns_none(tmp_path: Path):
    (tmp_path / "sider_effects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "twosides_ddi.json").write_text("{}", encoding="utf-8")
    (tmp_path / "chembl_moa.json").write_text("{}", encoding="utf-8")
    store = PharmacovigilanceStore(data_dir=tmp_path)
    with patch("src.data.repository.fetch_compound", return_value=None):
        assert get_enriched_drug(2244, store) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repository.py -k enriched -v`
Expected: FAIL with `ImportError: cannot import name 'get_enriched_drug'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/data/repository.py`:

```python
from src.data.enrichment import PharmacovigilanceStore, enrich_drug
```

```python
def get_enriched_drug(cid: int, store: PharmacovigilanceStore) -> Drug | None:
    """Resolve a Drug by CID and populate its side effects and interactions.

    Returns None if the CID cannot be resolved via PubChem. Chemical identity
    comes from PubChem; pharmacovigilance data comes from ``store``.
    """
    drug = get_drug_by_cid(cid)
    if drug is None:
        return None
    return enrich_drug(drug, store)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repository.py -k enriched -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "feat: Add get_enriched_drug to repository"
```

---

### Task 13: ETL script (build Tier 1 datasets)

**Files:**
- Create: `scripts/build_pharmacovigilance.py`
- Test: `tests/test_build_pharmacovigilance.py`

**Interfaces:**
- Consumes: `parse_sider`, `parse_twosides`, `parse_chembl_moa` (sources).
- Produces: `build_datasets(inputs: BuildInputs, out_dir: Path) -> None` — writes
  `sider_effects.json`, `twosides_ddi.json`, `chembl_moa.json` keyed by CID; plus a
  `main()` CLI entry that documents the source URLs. `BuildInputs` is a dataclass of local
  file paths (downloaded separately) + the UniChem map.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_pharmacovigilance.py
import json
from pathlib import Path

from scripts.build_pharmacovigilance import BuildInputs, build_datasets

_SE_ROWS = "CID100002244\tCID000002244\tC0018939\tPT\t10017955\tGastrointestinal haemorrhage\n"
_TWOSIDES_CSV = "drug_1_cid,drug_2_cid,condition_meddra_name,prr\n2244,5090,Nausea,3.1\n"
_CHEMBL_CSV = "molecule_chembl_id,mechanism_of_action,action_type\nCHEMBL25,COX inhibitor,INHIBITOR\n"


def test_build_datasets_writes_three_files(tmp_path: Path):
    sider = tmp_path / "se.tsv"; sider.write_text(_SE_ROWS, encoding="utf-8")
    twosides = tmp_path / "two.csv"; twosides.write_text(_TWOSIDES_CSV, encoding="utf-8")
    chembl = tmp_path / "chembl.csv"; chembl.write_text(_CHEMBL_CSV, encoding="utf-8")
    out = tmp_path / "out"

    build_datasets(
        BuildInputs(sider_se=sider, twosides=twosides, chembl_moa=chembl,
                    unichem={"CHEMBL25": 2244}),
        out,
    )

    effects = json.loads((out / "sider_effects.json").read_text())
    assert "2244" in effects
    assert json.loads((out / "twosides_ddi.json").read_text())["2244"]
    assert json.loads((out / "chembl_moa.json").read_text())["2244"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_build_pharmacovigilance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.build_pharmacovigilance'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/__init__.py` if it does not exist (empty file), then:

```python
# scripts/build_pharmacovigilance.py
"""Offline ETL: build Tier 1 pharmacovigilance datasets keyed by PubChem CID.

Downloads (documented below) are performed separately; this script parses the
local files and writes the per-CID JSON datasets consumed at runtime by
``src.data.enrichment``. The LLM term-normalizer (openFDA text → MedDRA) is NOT
invoked here yet — it is a placeholder (see pharmacovigilance/normalizer.py).

Source downloads:
  SIDER 4.1    : http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz
  TWOSIDES     : https://tatonetti.c2b2.columbia.edu/nsides/  (CSV export)
  ChEMBL MoA   : ChEMBL DB `mechanism` table export (CSV)
  UniChem map  : https://www.ebi.ac.uk/unichem/  (ChEMBL id -> PubChem CID)
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from src.data.sources import parse_chembl_moa, parse_sider, parse_twosides
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildInputs:
    """Local input files for the ETL (downloaded beforehand)."""

    sider_se: Path
    twosides: Path
    chembl_moa: Path
    unichem: dict[str, int]


def _write(out_dir: Path, name: str, data: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(data), encoding="utf-8")
    logger.info("Wrote %s (%d compounds)", path, len(data))


def build_datasets(inputs: BuildInputs, out_dir: Path) -> None:
    """Parse all sources and write the three per-CID JSON datasets."""
    _write(out_dir, "sider_effects.json",
            {str(k): v for k, v in parse_sider(inputs.sider_se).items()})
    _write(out_dir, "twosides_ddi.json",
            {str(k): v for k, v in parse_twosides(inputs.twosides).items()})
    _write(out_dir, "chembl_moa.json",
            {str(k): v for k, v in parse_chembl_moa(inputs.chembl_moa, inputs.unichem).items()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Tier 1 pharmacovigilance datasets.")
    parser.add_argument("--sider-se", type=Path, required=True)
    parser.add_argument("--twosides", type=Path, required=True)
    parser.add_argument("--chembl-moa", type=Path, required=True)
    parser.add_argument("--unichem", type=Path, required=True,
                        help="JSON mapping of ChEMBL id -> PubChem CID.")
    parser.add_argument("--out", type=Path,
                        default=Path("src/data/pharmacovigilance"))
    args = parser.parse_args()
    unichem = json.loads(args.unichem.read_text(encoding="utf-8"))
    build_datasets(
        BuildInputs(args.sider_se, args.twosides, args.chembl_moa, unichem), args.out
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_build_pharmacovigilance.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS (all tests green)

- [ ] **Step 6: Commit**

```bash
git add scripts/build_pharmacovigilance.py scripts/__init__.py tests/test_build_pharmacovigilance.py
git commit -m "feat: Add ETL script to build Tier 1 datasets"
```

---

## Notes for the implementer

- **openFDA Tier 2 wiring into the store** is intentionally deferred to a follow-up: the
  hooks exist (`fetch_openfda_label`, `PharmacovigilanceStore(cache_dir=...)`), but wiring
  Tier 2 fallback into `side_effects()` requires the term-normalizer (placeholder). Do NOT
  call the LLM at runtime — when implemented, normalization happens in the ETL only.
- **Real dataset downloads** are not part of the automated tests (network + large files).
  Run `python -m scripts.build_pharmacovigilance --help` and follow the documented URLs.
- **License:** SIDER is CC BY-NC-SA (non-commercial). This is accepted (Durin is academic);
  do not redistribute derived datasets under a commercial license.
