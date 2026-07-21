# notebooks/

Durin's interactive test bench. Explores the domain (ATC catalog, scraper,
models) without cluttering production code.

## Golden rule

A notebook is a **disposable lab**, not the product. Logic that matures here
**graduates to a module in `src/`** with its own tests. The service (FastAPI)
imports from `src/`, **never** from a notebook.

## Imports: why the bootstrap

An `.ipynb` is not part of any package, so a relative import
(`from ..src import ...`) fails with `attempted relative import with no known
parent package`. Since the notebook lives in `notebooks/`, its parent is the
repo root: adding it to `sys.path` lets you `from src... import` just like in
the tests.

First cell of every notebook:

```python
import sys
from pathlib import Path

ROOT = Path.cwd().parent          # notebooks/ -> repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

%load_ext autoreload
%autoreload 2                       # changes in src/ reload without restarting the kernel
```

(The scraper notebook also adds `ROOT / "scripts"` to the path.)

## Naming convention

```
<order>.<sub>-<initials>-<description>.ipynb
```

Examples: `1.0-adf-atc-scraper-and-catalog.ipynb`, `2.0-adf-pydantic-models.ipynb`.
`adf` is a placeholder for initials — swap it for your own.

## Running

Jupyter is not in the venv by default. Install it once:

```bash
uv pip install jupyter        # or: uv pip install jupyterlab
uv run jupyter lab            # opens the browser; run from notebooks/
```

Cells marked **OPTIONAL ⚠️** make network requests (PubChem / scraper) and are
commented out: they are not required to validate the notebook.

## Language

All notebook content — markdown, comments, print output, and example data
values — is written in **English**. Spanish is reserved for the i18n locale
catalogs (`src/locales/*.json5`) elsewhere in the project.

## Notebooks

| Notebook | What it tests |
|----------|----------------|
| `1.0-adf-atc-scraper-and-catalog.ipynb` | `codes.json` catalog (size, levels), the `ATCCode` model, and the scraper's pure functions (`parse_links`, `extract_code_from_href`) without network access. |
| `2.0-adf-pydantic-models.ipynb` | Valid construction of `ATCCode`/`Drug`/`Med`/`Patient`, derived properties, and the domain's 4 validation failures. |
| `3.0-adf-agency-models.ipynb` | Bootstrap stub for the agency layer (`agencies/`, CIMA/AEMPS) — not yet built out. |
| `4.0-adf-graph-enrichment.ipynb` | Pydantic models fed from the Neo4j pharmacovigilance graph via `src/data/enrichment.py`: CID discovery, `side_effects`/`interactions` as validated models, local `Drug` enrichment, and a full graph-fed `Patient`. |
| `5.0-adf-llm-normalizer-test.ipynb` | `LocalLLMNormalizer` end-to-end: SapBERT retrieval, llama.cpp inference (`[0-9]+` grammar), raw response inspection, and edge cases (NONE). |
