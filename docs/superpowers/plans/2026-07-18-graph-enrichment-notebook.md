# Graph-Enrichment Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `notebooks/4.0-adf-graph-enrichment.ipynb` exercising the domain pydantic models against the live Neo4j pharmacovigilance graph via the existing `PharmacovigilanceStore` / `enrich_drug` bridge.

**Architecture:** A notebook, no `src/` changes. Cells open `PharmacovigilanceStore`, guarded by `try/except`; on any connection/auth failure the store is set to `None` and every DB cell short-circuits with a printed skip message. When connected, cells discover a real sample CID from the graph, assemble validated `SideEffect` / `Interaction` / `Drug` models, and compose them up into a `Med` and a `Patient`.

**Tech Stack:** Python 3.14 (`.venv`, uv-managed), Pydantic v2, neo4j 6.2 driver, nbconvert/nbformat for headless execution.

## Global Constraints

- Interpreter: `.venv/bin/python` — bare `python` lacks the deps. Verify with `.venv/bin/python`.
- Markdown narration in Spanish; ALL code, inline `#` comments, AND user-facing `print(...)` status strings in English (user ruling 2026-07-18 — Spanish only in markdown cells and locale JSON5). Domain data values (patient/med names, disease strings) stay as-is.
- No edits to any file under `src/`. Notebook 2.0 stays untouched.
- Every cell that touches the DB must guard `if STORE is None: print(...skip...)` and never raise.
- Notebook must execute top-to-bottom with no uncaught exception whether Neo4j is up+authenticated, or down/unauthorized (current local state: running but `AuthError` on default creds).
- Neo4j coordinates come from `src.config.neo4j_config()` (env `NEO4J_*`). Never print the password.
- Reuse the existing bridge — do not reimplement Cypher or model assembly in the notebook.

---

### Task 1: Scaffold notebook — setup, connect, discover CID

**Files:**
- Create: `notebooks/4.0-adf-graph-enrichment.ipynb`

**Interfaces:**
- Consumes (from `src/`, already exists):
  - `src.data.enrichment.PharmacovigilanceStore(config=None)` — context manager; `.side_effects(cid) -> list[SideEffect]`, `.interactions(cid) -> list[Interaction]`, `.close()`.
  - `src.data.enrichment.enrich_drug(drug: Drug, store) -> Drug`.
  - `src.data.repository.get_enriched_drug(cid: int, store) -> Drug | None`.
  - `src.config.neo4j_config() -> Neo4jConfig(uri, user, password, database)`.
  - `src.data.pharmacovigilance.graph.driver(cfg) -> neo4j.Driver`.
  - `neo4j.exceptions.ServiceUnavailable`, `neo4j.exceptions.AuthError`.
- Produces (notebook globals later cells rely on): `STORE` (`PharmacovigilanceStore | None`), `SAMPLE_CID` (`int | None`).

- [ ] **Step 1: Create the notebook with the setup + connect + discover cells.**

Build the file with `nbformat` (deterministic, no kernel needed to author). Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python - <<'PY'
import nbformat as nbf
nb = nbf.v4.new_notebook()
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

cells = []
cells.append(md(
    "# 4.0 — Modelos Pydantic sobre el grafo Neo4j\n\n"
    "Ejercita los esquemas de `src/data/schemas/` alimentados desde el grafo de\n"
    "farmacovigilancia en **Neo4j** (migración SQLite→Neo4j). El puente ya existe\n"
    "en `src/data/enrichment.py`; aquí solo lo usamos y validamos.\n\n"
    "Si Neo4j no está accesible o autenticado, el notebook **omite** las celdas de\n"
    "BD con un mensaje y termina sin error."
))
cells.append(code(
    "import sys\n"
    "from pathlib import Path\n\n"
    "ROOT = Path.cwd().parent  # the notebook lives in notebooks/ -> repo root\n"
    "if str(ROOT) not in sys.path:\n"
    "    sys.path.insert(0, str(ROOT))\n\n"
    "%load_ext autoreload\n"
    "%autoreload 2\n\n"
    "from datetime import date\n\n"
    "from neo4j.exceptions import AuthError, ServiceUnavailable\n\n"
    "from src.config import neo4j_config\n"
    "from src.data.enrichment import PharmacovigilanceStore, enrich_drug\n"
    "from src.data.repository import get_enriched_drug\n"
    "from src.data.schemas import ATCCode, Drug, Interaction, Med, Patient, SideEffect\n\n"
    "cfg = neo4j_config()\n"
    "print(f\"uri={cfg.uri}  user={cfg.user}  database={cfg.database}\")  # never the password"
))
cells.append(md(
    "## Conexión y descubrimiento de un CID\n\n"
    "Abre el `PharmacovigilanceStore`. Ante fallo de conexión o autenticación,\n"
    "`STORE = None` y las celdas siguientes se omiten. Con conexión, busca un CID\n"
    "de `:Drug` con al menos una arista `HAS_SIDE_EFFECT` (prefiere 33613,\n"
    "amoxicilina; si no, el primero encontrado)."
))
cells.append(code(
    "STORE: PharmacovigilanceStore | None = None\n"
    "SAMPLE_CID: int | None = None\n\n"
    "_DISCOVER = (\n"
    "    \"MATCH (d:Drug)-[:HAS_SIDE_EFFECT]->() \"\n"
    "    \"RETURN d.cid AS cid ORDER BY (d.cid = 33613) DESC, d.cid LIMIT 1\"\n"
    ")\n\n"
    "try:\n"
    "    STORE = PharmacovigilanceStore(cfg)\n"
    "    with STORE._driver.session(database=cfg.database) as _s:\n"
    "        _rec = _s.run(_DISCOVER).single()\n"
    "    SAMPLE_CID = _rec[\"cid\"] if _rec else None\n"
    "    if SAMPLE_CID is None:\n"
    "        print(\"Neo4j connected but no HAS_SIDE_EFFECT edges — skipping the DB.\")\n"
    "    else:\n"
    "        print(f\"Connected. Sample CID = {SAMPLE_CID}\")\n"
    "except (ServiceUnavailable, AuthError) as exc:\n"
    "    if STORE is not None:\n"
    "        STORE.close()\n"
    "    STORE = None\n"
    "    print(f\"Neo4j unavailable ({type(exc).__name__}) — skipping DB cells.\")"
))
nb["cells"] = cells
nb["metadata"] = {"language_info": {"name": "python"}}
nbf.write(nb, "notebooks/4.0-adf-graph-enrichment.ipynb")
print("written", len(nb["cells"]), "cells")
PY
```

Expected: `written 4 cells`.

Note on `STORE._driver`: the store exposes no public raw-session accessor and adding one would edit `src/`. The discovery query is notebook-only diagnostic Cypher, so reaching the driver attribute here is deliberate and acceptable within a notebook.

- [ ] **Step 2: Execute the notebook headless; verify it runs without error.**

Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python -m nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.timeout=120 \
  notebooks/4.0-adf-graph-enrichment.ipynb && echo "EXECUTE_OK"
```

Expected: `EXECUTE_OK`. In the current local state (Neo4j up but default creds → `AuthError`) the connect cell prints `Neo4j no disponible (AuthError) — se omiten las celdas de BD.` and the notebook completes. No traceback in any cell.

- [ ] **Step 3: Confirm no error output landed in the notebook.**

Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python - <<'PY'
import nbformat as nbf
nb = nbf.read("notebooks/4.0-adf-graph-enrichment.ipynb", as_version=4)
errs = [o for c in nb.cells if c.cell_type=="code"
        for o in c.get("outputs",[]) if o.get("output_type")=="error"]
print("errors:", len(errs))
assert not errs, errs
print("NO_ERRORS")
PY
```

Expected: `errors: 0` then `NO_ERRORS`.

- [ ] **Step 4: Commit.**

```bash
cd /home/astordna/Documentos/Projects/Durin
git add notebooks/4.0-adf-graph-enrichment.ipynb
git commit -m "feat: Scaffold Neo4j graph-enrichment notebook"
```

---

### Task 2: Model-assembly sections — effects, interactions, Drug, Patient, optional PubChem, teardown

**Files:**
- Modify: `notebooks/4.0-adf-graph-enrichment.ipynb` (append cells)

**Interfaces:**
- Consumes: `STORE`, `SAMPLE_CID` (from Task 1); the `src/` symbols already imported in the setup cell.
- Produces: final notebook — no downstream consumers.

- [ ] **Step 1: Append the assembly, Patient, optional-PubChem and teardown cells.**

Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python - <<'PY'
import nbformat as nbf
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell
nb = nbf.read("notebooks/4.0-adf-graph-enrichment.ipynb", as_version=4)

nb.cells.append(md(
    "## Filas del grafo → modelos validados\n\n"
    "`side_effects` / `interactions` devuelven modelos Pydantic ya validados\n"
    "(provenance obligatoria, código MedDRA con patrón, literales de severidad y\n"
    "de fuente). Comprobamos el tipo y mostramos los primeros."
))
nb.cells.append(code(
    "if STORE is None or SAMPLE_CID is None:\n"
    "    print(\"DB skipped — no effects/interactions to show.\")\n"
    "else:\n"
    "    effects = STORE.side_effects(SAMPLE_CID)\n"
    "    interactions = STORE.interactions(SAMPLE_CID)\n"
    "    assert all(isinstance(e, SideEffect) for e in effects)\n"
    "    assert all(isinstance(i, Interaction) for i in interactions)\n"
    "    print(f\"CID {SAMPLE_CID}: {len(effects)} effects, {len(interactions)} interactions\")\n"
    "    for e in effects[:3]:\n"
    "        print(e.model_dump())\n"
    "    for i in interactions[:3]:\n"
    "        print(i.model_dump())"
))
nb.cells.append(md(
    "## Enriquecer un Drug local (sin red)\n\n"
    "Construimos un `Drug` a mano y lo enriquecemos desde el grafo con\n"
    "`enrich_drug`. Camino principal, sin depender de PubChem."
))
nb.cells.append(code(
    "enriched: Drug | None = None\n"
    "if STORE is None or SAMPLE_CID is None:\n"
    "    print(\"DB skipped — Drug not enriched.\")\n"
    "else:\n"
    "    base = Drug(cid=SAMPLE_CID, name=f\"CID {SAMPLE_CID}\")\n"
    "    enriched = enrich_drug(base, STORE)\n"
    "    print(f\"has_atc={enriched.has_atc}  \"\n"
    "          f\"effects={len(enriched.side_effects)}  \"\n"
    "          f\"interactions={len(enriched.interactions)}\")"
))
nb.cells.append(md(
    "## Paciente completo desde el grafo\n\n"
    "Envolvemos el `Drug` enriquecido en un `Med` y un `Patient` (misma forma que\n"
    "el notebook 2.0). Demuestra que los modelos alimentados por el grafo componen\n"
    "toda la jerarquía del dominio y siguen pasando cada validador."
))
nb.cells.append(code(
    "if enriched is None:\n"
    "    print(\"DB skipped — Patient not built.\")\n"
    "else:\n"
    "    med = Med(\n"
    "        ATC_code=(enriched.chemical_group if enriched.has_atc else ATCCode(code=\"J01CA04\")),\n"
    "        name=\"Medicación de prueba\",\n"
    "        dosage=\"500 mg\",\n"
    "        frequency=\"cada 8h\",\n"
    "        start_date=date(2026, 6, 1),\n"
    "        active_principles=[enriched],\n"
    "    )\n"
    "    patient = Patient(\n"
    "        id=1,\n"
    "        name=\"Paciente de prueba\",\n"
    "        age=78,\n"
    "        birth_date=date(1948, 1, 1),\n"
    "        number_of_meds=1,\n"
    "        polymedicated=True,\n"
    "        diseases=[\"infección respiratoria\"],\n"
    "        medication=[med],\n"
    "    )\n"
    "    print(patient.model_dump())"
))
nb.cells.append(md(
    "## Opcional — PubChem + grafo ⚠️\n\n"
    "`get_enriched_drug` resuelve la identidad química vía **PubChem (red)** y le\n"
    "añade la farmacovigilancia del grafo. No hace falta ejecutarla para validar\n"
    "el notebook."
))
nb.cells.append(code(
    "if STORE is None or SAMPLE_CID is None:\n"
    "    print(\"DB skipped — PubChem not queried.\")\n"
    "else:\n"
    "    full = get_enriched_drug(SAMPLE_CID, STORE)\n"
    "    print(full.model_dump() if full else \"CID not resolved in PubChem\")"
))
nb.cells.append(md("## Cierre"))
nb.cells.append(code(
    "if STORE is not None:\n"
    "    STORE.close()\n"
    "    print(\"Store closed.\")\n"
    "else:\n"
    "    print(\"No store was open.\")"
))

nbf.write(nb, "notebooks/4.0-adf-graph-enrichment.ipynb")
print("total cells", len(nb.cells))
PY
```

Expected: `total cells 14`.

- [ ] **Step 2: Re-execute the whole notebook headless.**

Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python -m nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.timeout=180 \
  notebooks/4.0-adf-graph-enrichment.ipynb && echo "EXECUTE_OK"
```

Expected: `EXECUTE_OK`. With the current local DB state each DB cell prints its skip message; with an authenticated populated DB, sections show real model dumps. No traceback either way.

- [ ] **Step 3: Confirm zero error outputs.**

Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
.venv/bin/python - <<'PY'
import nbformat as nbf
nb = nbf.read("notebooks/4.0-adf-graph-enrichment.ipynb", as_version=4)
errs = [o for c in nb.cells if c.cell_type=="code"
        for o in c.get("outputs",[]) if o.get("output_type")=="error"]
print("errors:", len(errs))
assert not errs, errs
print("NO_ERRORS")
PY
```

Expected: `errors: 0` then `NO_ERRORS`.

- [ ] **Step 4: Optional live-DB smoke test (only if a password is available).**

If the user provides `NEO4J_PASSWORD`, verify the live path exercises real models. Run:

```bash
cd /home/astordna/Documentos/Projects/Durin
NEO4J_PASSWORD='<user-password>' .venv/bin/python -m nbconvert --to notebook \
  --execute --inplace --ExecutePreprocessor.timeout=300 \
  notebooks/4.0-adf-graph-enrichment.ipynb && echo "LIVE_OK"
```

Expected: `LIVE_OK`, and the connect cell prints `Conectado. CID de muestra = <n>`. Skip this step if no password — the graceful-skip path is already validated by Steps 2–3.

- [ ] **Step 5: Commit.**

```bash
cd /home/astordna/Documentos/Projects/Durin
git add notebooks/4.0-adf-graph-enrichment.ipynb
git commit -m "feat: Add graph-fed model assembly to notebook 4.0"
```

---

## Self-Review

**Spec coverage:** Setup (T1 setup cell) ✓; Connect+discover with graceful skip (T1) ✓; Graph→validated models (T2 §effects) ✓; Enrich local Drug no-network (T2 §enrich) ✓; Full Patient (T2 §patient) ✓; Optional PubChem (T2 §optional) ✓; Teardown (T2 §cierre) ✓; Fallback guards on every DB cell ✓; Success criteria = headless execute + zero-error assertion (T1/T2 Steps 2–3) ✓.

**Placeholder scan:** No TBD/TODO. `'<user-password>'` in T2 Step 4 is an explicit user-supplied value in an optional step, not a plan gap.

**Type consistency:** `STORE: PharmacovigilanceStore | None`, `SAMPLE_CID: int | None`, `enriched: Drug | None` consistent across cells. `Med.ATC_code` is an `ATCCode` (nested model), not a str — fixed: the Patient cell passes `enriched.chemical_group` (already an `ATCCode`) or `ATCCode(code="J01CA04")`, and `ATCCode` is imported in the setup cell.
