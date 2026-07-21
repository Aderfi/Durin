# Diseño: orquestación del ETL offline con Snakemake

**Fecha:** 2026-07-21
**Alcance:** un `Snakefile` único en la raíz que encadena los scripts de adquisición/
transformación ya existentes (`scripts/atc_scraper.py`, `scripts/icd10_scraper.py`,
`scripts/json_plain.py`, `scripts/build_rxnorm_map.py`, `scripts/build_pharmacovigilance.py`)
hasta dejar listas las CSVs de `tmp/neo4j_import/` para `neo4j-admin database import`.
**No** automatiza el import en sí ni `recover_chembl_cids.py`.

> Convención: este spec va en español (como los previos); todo el código
> (Snakefile, docstrings, comentarios) va en inglés, siguiendo la regla ya
> establecida en el proyecto.

## Contexto

El pipeline offline ya existe como una cadena de scripts independientes, cada uno
invocado a mano en el orden correcto con las rutas correctas. Eso funciona pero no es
reproducible ni auto-documentado: no hay forma de saber, mirando el repo, qué
produce qué, ni de re-ejecutar solo la parte que cambió. `tmp/` ya contiene los
artefactos de una corrida manual completa (SIDER, TWOSIDES, ChEMBL MoA, UniChem,
rxnorm map, y las 6 CSVs de `tmp/neo4j_import/`), lo que sirve como caso de prueba
real para validar el DAG sin gastar red.

**Decisión previa que se respeta** (`_management/2026-07-18-pharmacovigilance-neo4j-migration.md`):
el paso `neo4j-admin database import full` quedó retenido a propósito como manual —
requiere el servicio Neo4j parado, `sudo`, y sobreescribe la base de datos. Snakemake
no lo automatiza.

## Decisiones cerradas (brainstorming)

- **Alcance: hasta las CSVs.** El DAG termina en `tmp/neo4j_import/*.csv`. El import
  y `recover_chembl_cids.py` (que además escribe en una DB ya viva, rompiendo el
  modelo de DAG por ficheros de Snakemake) quedan fuera, sin cambios.
- **Adquisición de datos crudos: mixta.** Solo SIDER tiene URL de fichero estable
  (`https://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz`) → regla propia.
  TWOSIDES/ChEMBL MoA/UniChem son exports de portal sin URL fija → inputs manuales
  fijos en `tmp/`; si faltan, el `MissingInputException` nativo de Snakemake ya
  nombra el fichero exacto — no se añade mensajería custom encima.
- **Entorno de ejecución: host, vía `uv`.** `snakemake` se añade al grupo `dev` de
  `pyproject.toml`. No se integra con el Docker de esta sesión — el ETL hace red
  (scraping, descargas) y no hay necesidad de contenerizarlo ahora.
- **Estructura: `Snakefile` único en la raíz + `config.yaml`.** El pipeline tiene
  ~7 reglas; un layout modular (`workflow/rules/*.smk`) es prematuro para este
  tamaño y se puede migrar sin reescribir reglas si crece (más agencias, Tier 2
  openFDA automatizado, etc.).
- **SIDER `.gz` sin descomprimir.** `parse_sider` (`src/data/sources.py`) usa
  `pl.read_csv`, que detecta `.gz` por extensión y descomprime en memoria —
  verificado. La regla de descarga no necesita un paso de descompresión aparte.

## Cambio de código necesario: `scripts/json_plain.py`

Hoy usa constantes de módulo hardcodeadas (`_FILE_NAME = "icd_codes.json"`,
`_FILE_OUTPUT = Path("plain_icd_codes.json")`), rutas relativas al cwd — no encaja
como regla de Snakemake con `input`/`output` declarados. Se le añade `argparse`
siguiendo el mismo patrón que ya usan `atc_scraper.py` y `build_rxnorm_map.py`:

```python
parser.add_argument("--input", type=Path, default=Path("scripts/icd_codes.json"))
parser.add_argument("--output", type=Path, default=Path("scripts/plain_icd_codes.json"))
```

Comportamiento idéntico; solo se parametrizan las rutas. Las ubicaciones por
defecto no cambian (siguen en `scripts/`, no se mueven a `src/data/`, porque no
existe todavía un módulo `icd10` consumidor — moverlas sería scope creep).

## Reglas del `Snakefile`

```
rule all:
    input:
        "src/data/atc/codes.json",
        "scripts/plain_icd_codes.json",
        expand("tmp/neo4j_import/{f}", f=[
            "drugs.csv", "adverse_effects.csv", "mechanisms.csv",
            "has_side_effect.csv", "has_mechanism.csv",
            "interacts_with_header.csv", "interacts_with.csv",
        ])

rule scrape_atc_catalog:
    output: "src/data/atc/codes.json"
    shell: "python scripts/atc_scraper.py --output {output} --checkpoint scripts/checkpoint.json"

rule scrape_icd10:
    output: "scripts/icd_codes.json"
    shell: "python scripts/icd10_scraper.py --output {output}"

rule flatten_icd10:
    input: "scripts/icd_codes.json"
    output: "scripts/plain_icd_codes.json"
    shell: "python scripts/json_plain.py --input {input} --output {output}"

rule download_sider:
    output: "tmp/raw/meddra_all_se.tsv.gz"
    params: url=config["sider_url"]
    shell: "mkdir -p tmp/raw && curl -fsSL {params.url} -o {output}"

rule build_rxnorm_map:
    input: twosides="tmp/TWOSIDES.csv"
    output: "tmp/rxnorm_to_cid.tsv"
    shell: "python scripts/build_rxnorm_map.py --twosides {input.twosides} --out {output}"

rule build_pharmacovigilance_csvs:
    input:
        sider="tmp/raw/meddra_all_se.tsv.gz",
        twosides="tmp/TWOSIDES.csv",
        chembl="tmp/chembl_moa.csv",
        unichem="tmp/src1src22.txt",
        rxnorm="tmp/rxnorm_to_cid.tsv",
    output:
        expand("tmp/neo4j_import/{f}", f=[
            "drugs.csv", "adverse_effects.csv", "mechanisms.csv",
            "has_side_effect.csv", "has_mechanism.csv",
            "interacts_with_header.csv", "interacts_with.csv",
        ])
    params: out_dir="tmp/neo4j_import"
    shell:
        "python -m scripts.build_pharmacovigilance "
        "--sider-se {input.sider} --twosides {input.twosides} "
        "--chembl-moa {input.chembl} --unichem {input.unichem} "
        "--rxnorm {input.rxnorm} --out-dir {params.out_dir}"
```

`tmp/TWOSIDES.csv`, `tmp/chembl_moa.csv`, `tmp/src1src22.txt` no tienen regla
productora — son inputs manuales; Snakemake falla con `MissingInputException` si
no están.

## `config.yaml`

```yaml
sider_url: "https://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz"
tmp_dir: "tmp"
neo4j_import_dir: "tmp/neo4j_import"
```

Mínimo a propósito — el resto de parámetros ya tiene defaults sensatos en cada
script y no se duplican en config solo por uniformidad.

## Manejo de errores

- Input manual ausente (TWOSIDES/ChEMBL/UniChem) → `MissingInputException` nativo
  de Snakemake, nombra el fichero exacto. Sin capa de mensajería propia.
- Fallo de red en `download_sider` o en los scrapers → el `shell:` termina con
  código de salida no-cero, Snakemake marca la regla como fallida y detiene esa
  rama del DAG (comportamiento por defecto, sin `retry` — los scrapers ya tienen
  su propio checkpoint/resume interno para reintentos manuales).
- `openFDA` (Tier 2, con el `LocalLLMNormalizer`) sigue **fuera** del DAG — es
  bajo demanda, no parte de la corrida batch.

## Testing / verificación

- `tmp/` ya tiene los artefactos de una corrida real completa → `snakemake -n`
  (dry-run) valida el DAG completo (todas las reglas, todas las dependencias)
  sin red ni tiempo de cómputo.
- `snakemake -R flatten_icd10` fuerza el re-run del único paso que cambió
  (el refactor argparse de `json_plain.py`) y confirma que produce el mismo
  `plain_icd_codes.json` que la versión hardcodeada.
- `uv run pytest` sigue en verde — el refactor de `json_plain.py` no toca lógica,
  solo la parametrización de rutas.

## Fuera de alcance

- `neo4j-admin database import full` — sigue manual (decisión previa).
- `recover_chembl_cids.py` — corre después del import, sobre una DB ya viva.
- Automatización de la descarga de TWOSIDES/ChEMBL MoA/UniChem — son exports de
  portal sin URL de fichero estable.
- Integración con Docker — el ETL corre en host vía `uv run snakemake`.
- Tier 2 openFDA / normalización LLM bajo demanda — no es parte de la corrida batch.
