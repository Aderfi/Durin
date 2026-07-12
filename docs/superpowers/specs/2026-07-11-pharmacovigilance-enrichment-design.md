# Diseño: enriquecimiento de farmacovigilancia (efectos adversos + mecanismo)

**Fecha:** 2026-07-11
**Alcance:** capa de adquisición y ensamblado de efectos adversos y mecanismo por
compuesto (CID), fuente de datos del futuro motor de riesgo. Añade módulos nuevos
(`agencies/`, `pharmacovigilance/`, `sources.py`, `enrichment.py`, script ETL, logging),
amplía `drug.py` y `types.py`. **No** implementa el motor de riesgo (siguiente proyecto).

> Convención: este spec va en español (como los previos); **todos los docstrings y
> comentarios del código van en inglés**. Locale JSON5 es el único sitio con español.

## Contexto

El motor de riesgo depende por completo de la calidad de dos datos por fármaco:
**efectos adversos** (`SideEffect`) y **mecanismo/interacciones** (`Interaction.mechanism`).
La regla ya establecida en el proyecto se mantiene: *los modelos solo validan; el I/O y
el enriquecimiento viven fuera (`repository`/servicios)*. La identidad química es el
**PubChem CID** (`Drug.cid`), y ya existen catálogos locales (`atc_codes.json`,
`icd10_codes.json`) como precedente del patrón "dataset local keyed by X".

## Decisiones cerradas (brainstorming)

- **Rigor: híbrido con provenance.** Las fuentes estructuradas son la verdad; el LLM solo
  normaliza/mapea texto a vocabulario (MedDRA), nunca inventa hechos. **Cada hecho clínico
  lleva su procedencia** (`Provenance`), invariante del motor de riesgo.
- **Universo: abierto por CID.** Cualquier compuesto se resuelve vía PubChem. Acotado en la
  práctica por lo que las fuentes cubren, no por un catálogo cerrado.
- **Capa de agencias enchufable.** Registro de agencias nacionales de medicamentos, una por
  país, seleccionable. **CIMA/AEMPS (ES)** implementada ahora; el resto y la normalización de
  sus diferencias estructurales se difiere.
- **Enfoque C — híbrido escalonado.** Tier 1 dataset local pre-construido + Tier 2 openFDA
  bajo demanda con cache. LLM confinado al ETL offline; runtime nunca llama LLM.
- **Licencia: no comercial / académico.** SIDER (CC BY-NC-SA) entra como Tier 1 pleno.
- **LLM: implementado (retrieve-then-rank).** El normalizador de términos ya no es placeholder.
  Sigue el estándar de *medical concept normalization*: (1) generación de candidatos con un
  bi-encoder biomédico (**SapBERT**) sobre el vocabulario MedDRA cerrado, (2) reranking con un
  LLM local vía Ollama que devuelve el **índice** de un candidato (o 0 = NONE). El código sale
  de la tabla local por índice, nunca del LLM → alucinación de código imposible. Dependencia
  pesada (`sentence-transformers`) confinada al grupo `extras`; el LLM sigue confinado al ETL
  offline (runtime jamás lo llama).

## Reparto de capas

| Capa | Módulo | Fuente | Aporta |
|------|--------|--------|--------|
| Producto / formulario (`Med`) | `agencies/` | CIMA/AEMPS (ES), enchufable | Qué se comercializa, nombre comercial, ATC, principios activos |
| Identidad química (`Drug`, `cid`) | `repository.py` (ya existe) | PubChem | CID, fórmula, SMILES, InChIKey |
| Farmacovigilancia (`SideEffect`, `Interaction`) | `pharmacovigilance/`, `enrichment.py` | SIDER, ChEMBL, TWOSIDES, openFDA | Efectos adversos (MedDRA) + mecanismo + interacciones, keyed by CID |

Pipeline:

```
agency (CIMA) ─ producto → principios activos (name, ATC)
   → PubChem: name → CID
   → enrichment.enrich_drug(cid): Tier 1 local → (si falta) Tier 2 openFDA + cache
   → Drug enriquecido: [SideEffect + provenance], [Interaction + provenance]
```

## Disposición de módulos

```
src/data/
  atc/                          # ya existe
  agencies/                     # NUEVO: capa producto/formulario, enchufable por país
    __init__.py                 # AGENCIES = {"ES": CimaAdapter()}
    base.py                     # AgencyAdapter (interfaz común)
    cima.py                     # adaptador CIMA/AEMPS (implementado ahora)
  pharmacovigilance/            # NUEVO: Tier 1 datasets keyed by CID + normalizador
    sider_effects.json          # efectos adversos por CID (MedDRA)     ← ETL
    chembl_moa.json             # mecanismo de acción por CID           ← ETL
    twosides_ddi.json           # interacciones por par de CID          ← ETL
    normalizer.py               # TermNormalizer (interfaz) + placeholder LLM local
    _cache/                     # Tier 2 openFDA bajo demanda, por CID
  sources.py                    # NUEVO: adaptadores de fuente (SIDER/ChEMBL/openFDA/UniChem)
  enrichment.py                 # NUEVO: orquesta Tier1→Tier2, ensambla los modelos
src/utils/
  logging.py                    # NUEVO: config central de logging (consola + logs/)
scripts/
  build_pharmacovigilance.py    # NUEVO: ETL offline (descarga + mapeo CID + normaliza + escribe)
logs/                           # NUEVO (gitignored): salida de logs a archivo
```

## Cambios de modelo

### `types.py` (nuevos)

```python
SourceName = Literal[
    "SIDER", "ChEMBL", "TWOSIDES", "openFDA", "CIMA", "LLM_NORMALIZED"
]
MedDRACode = Annotated[str, ...]   # numeric MedDRA code pattern, e.g. "10017955"
```

### `Provenance` (nuevo, en `drug.py`) — trazabilidad por hecho

```python
class Provenance(DomainModel):
    source: SourceName                # where the datum comes from
    source_id: str | None = None      # native id (STITCH, ChEMBL molregno, openFDA set_id);
                                      # for LLM_NORMALIZED holds the original free text
    retrieved: date | None = None     # extraction date (ETL run or Tier 2 cache write)
```

### `SideEffect` (ampliado)

```python
name: NonEmptyStr                                 # existente
meddra_pt: NonEmptyStr | None = None              # NUEVO: MedDRA Preferred Term
meddra_code: MedDRACode | None = None             # NUEVO: MedDRA code
severity: SeverityLevel | None = None              # CAMBIO: ahora opcional (fuente no siempre lo da)
severity_derived: bool = False                     # NUEVO: True si severity se dedujo (no vino de la fuente)
frequency: FrequencyCategory | None = None         # existente
provenance: Provenance                             # NUEVO: obligatorio
```

### `Interaction` (ampliado)

```python
# campos actuales intactos (interacting_drug_id, interacting_drug, interaction_type,
# severity, mechanism, description, management, require_drug_identity)
provenance: Provenance                             # NUEVO: obligatorio
```

### `Product` (nuevo, `agencies/base.py`) — capa agencia, mínimo hoy

```python
class Product(DomainModel):
    national_code: str                             # national registry code (CIMA nº registro)
    name: NonEmptyStr                              # brand/product name
    atc: ATCCode | None = None
    active_principle_names: list[NonEmptyStr]      # PubChem resolves these to CIDs
```

Invariantes:
- `provenance` **obligatorio** en `SideEffect`/`Interaction`: imposible un hecho sin fuente.
- `Provenance` y `Product` son `DomainModel` puros (solo validan), coherentes con la regla.
- MedDRA opcional en `SideEffect`: SIDER lo trae; openFDA (Tier 2) puede no traerlo hasta
  que el normalizador lo mapee (`source="LLM_NORMALIZED"`).

## Capa de agencias (`agencies/`)

`AgencyAdapter` — contrato común, cada agencia lo implementa:

```python
class AgencyAdapter(Protocol):
    def lookup_product(self, query: str) -> list[Product]: ...          # search national catalog
    def get_active_principles(self, product: Product) -> list[Product]: ... # product → principles
```

- Registro `AGENCIES = {"ES": CimaAdapter()}`; selección por código de país (default `"ES"`).
- `CimaAdapter` (`cima.py`): consume la API REST de CIMA (AEMPS). I/O + `tenacity` retry.
- Diferencias estructurales entre agencias → cada adaptador normaliza a `Product`. Trabajo
  diferido; hoy solo CIMA cumple el contrato.

## Fuentes: mapeo a CID, licencias, formato

**SIDER 4.1** — efectos adversos de fichas técnicas (Tier 1)
- Archivos: `meddra_all_se.tsv.gz` (efecto + MedDRA PT/LLT), `meddra_freq.tsv.gz` (frecuencia).
- ID→CID: STITCH IDs (`CID1xxxxxxx` flat / `CID0xxxxxxx` stereo). Transform determinista:
  quitar prefijo `CID`, quitar ceros a la izquierda → PubChem CID. Usamos **flat** (`CID1`)
  para alinear con `Drug.inchikey_skeleton` (agrupa estereoisómeros).
- Trae MedDRA nativo → `meddra_pt` / `meddra_code` directos. `source="SIDER"`.
- Frecuencia: `meddra_freq.tsv.gz` da rangos → map a `FrequencyCategory`.
- ⚠️ **SIDER no aporta `severity`.** Ver "Severidad" abajo.
- Licencia **CC BY-NC-SA 4.0** (no comercial) — aceptada (Durin académico).

### Severidad (opcional + derivada)

Ninguna fuente da un `severity` mild/moderate/severe limpio por efecto. Política:

- `SideEffect.severity` es **opcional** (`SeverityLevel | None`).
- El ETL **deriva** severity de forma **determinista** (no LLM) cuando hay señal MedDRA: un
  efecto cuyo MedDRA PT es *Important Medical Event* / pertenece a un *SMQ* serio, o marcado
  "serious" en FAERS → `severe`; con señal contraria → `moderate`; sin señal alguna →
  `None`.
- Cuando el valor viene de esa derivación (no de la fuente directa), `severity_derived=True`.
  Así el motor de riesgo distingue "grave según la fuente" de "grave inferido".
- La derivación es una regla determinista: NO usa el `TermNormalizer`/LLM ni la marca
  `source="LLM_NORMALIZED"`; `provenance.source` sigue siendo la fuente del efecto.

**ChEMBL** — mecanismo de acción (Tier 1)
- Tabla `mechanism_of_action` + `molecule_dictionary`. ID→CID vía **UniChem** (molregno →
  PubChem CID). Licencia **CC BY-SA 3.0**. `source="ChEMBL"`.

**TWOSIDES / OFFSIDES** (nsides.io) — interacciones fármaco-fármaco (Tier 1)
- Eventos adversos por **par** de fármacos + estadística (PRR) → `Interaction`. CIDs nativos.
  Licencia **CC0**. `source="TWOSIDES"`.

**openFDA** — labels (Tier 2, bajo demanda)
- Endpoints `drug/label` (`adverse_reactions`, `mechanism_of_action`) y `drug/event` (FAERS).
- Keyed por producto → map a CID vía nombre de principio activo → PubChem.
- Texto libre → normalizador de términos (placeholder LLM local) → MedDRA. `source="openFDA"`
  para el hecho, `source="LLM_NORMALIZED"` para el código MedDRA derivado.
- Dominio público, rate-limited → cache local (`_cache/`) + `tenacity` retry.

Formato Tier 1 (ej. `sider_effects.json`, CID string como clave):

```json
{
  "2244": [
    {"name": "gastrointestinal haemorrhage", "meddra_pt": "Gastrointestinal haemorrhage",
     "meddra_code": "10017955", "severity": "severe", "severity_derived": true,
     "frequency": "rare", "source": "SIDER", "source_id": "CID100002244",
     "retrieved": "2026-07-11"}
  ]
}
```

`enrichment.py` lee estos JSON y ensambla `SideEffect`/`Interaction` con su `Provenance`.

## Frontera del LLM

Regla dura: **el LLM nunca afirma un hecho clínico; solo codifica texto ya afirmado por una
fuente.**

- **Dónde**: solo en el ETL (`scripts/build_pharmacovigilance.py`) vía `normalizer.py`.
  Runtime jamás llama LLM; consume el `openfda_effects.json` ya construido offline.
- **Arquitectura** (`pharmacovigilance/normalizer.py`) — *retrieve-then-rank*:

  ```python
  class TermNormalizer(Protocol):
      def normalize(self, text: str) -> tuple[str, MedDRACode] | None: ...

  class CandidateGenerator(Protocol):
      def candidates(self, text: str, k: int) -> list[tuple[str, MedDRACode]]: ...

  class SapBERTCandidateGenerator:
      """Dense retrieval: SapBERT embeds the closed vocab once, cosine top-K."""

  class LocalLLMNormalizer:
      """Ranks the retrieved shortlist with a local Ollama LLM.

      The LLM returns the *index* of one candidate (or 0 = NONE); the (PT, code)
      pair is read from the local candidate table, so it can neither invent a
      code nor choose outside the closed vocabulary.
      """
  ```

- **Qué hace**: (1) SapBERT recupera los top-K MedDRA PT más cercanos al texto (capta jerga
  de labels que el matching léxico pierde: "can't sleep" → "Insomnia"); (2) el LLM elige uno.
- **Qué NO hace**: no decide "fármaco X causa efecto Y" — esa aserción viene del label.
- **Vocabulario cerrado**: el LLM solo elige entre los candidatos recuperados; nunca emite el
  código. Índice fuera de rango / 0 / no numérico → `None` (se descarta, nunca se mis-codifica).
- **Trazabilidad**: salida `source="LLM_NORMALIZED"`, texto original en `source_id`.
- **Determinismo**: `temperature=0.0`.
- **Dependencia**: `sentence-transformers` (SapBERT) en el grupo `extras`; import perezoso →
  el paquete core no arrastra torch.

> **Desviación consciente vs. el plan original.** El plan describía el output como *nombre de
> PT* con lookup exacto `meddra_terms[answer]`. Se adoptó **índice de candidato** en su lugar:
> misma garantía ("código de la tabla local, alucinación imposible") con **mejor recall** —
> un modelo pequeño devuelve un entero de forma fiable, no re-teclea un PT de 19 chars sin
> deriva de formato/mayúsculas que dispararía falsos `None`. El principio del plan se respeta;
> solo mejora su codificación.

## Logging

Config central en `src/utils/logging.py`, usada por todos los módulos:

- Todos los módulos usan `logging.getLogger(__name__)`.
- Handlers: **consola** (`StreamHandler`) **y archivo** en `logs/` (`RotatingFileHandler`).
- Formato con timestamp, nivel, módulo, mensaje.
- `logs/` en `.gitignore`.
- **Sin errores silenciosos**: todo fallo se registra.
  - Fallo de mapeo ID→CID en el ETL → `logger.warning` con el id ofensor y la fuente, luego
    skip (no rompe el ETL, pero queda en el log).
  - Red / rate-limit openFDA / CIMA → `logger.warning` + retry (`tenacity`); agotado → `error`.

## Errores

- CID sin datos en Tier 1 → Tier 2; sin datos en ninguno → `side_effects=[]` **válido**
  (ausencia ≠ error; nunca se fabrica un hecho).
- Fallo de mapeo ID→CID → log (warning) + skip, no rompe el ETL.
- openFDA/CIMA red o rate-limit → retry con `tenacity` (ya es dep) + cache; agotado → log error.

## Testing / verificación

- Transform STITCH→CID: unit tests (`CID100002244` → `2244`, `CID000002244` → `2244`).
- `enrichment.enrich_drug(cid)` con fixtures Tier 1 (sin red) → ensambla `SideEffect` /
  `Interaction` con `provenance` correcto.
- `Provenance` obligatorio: construir `SideEffect` sin `provenance` → `ValidationError`.
- Severidad: `SideEffect` sin señal MedDRA → `severity=None`; con señal serio → `severity="severe"`,
  `severity_derived=True`.
- Contrato `AgencyAdapter`: `CimaAdapter` mockeado (respuesta HTTP fija) → `Product` válido.
- `TermNormalizer`: `LocalLLMNormalizer.normalize` con `CandidateGenerator` falso + cliente
  Ollama stub (sin torch, sin red) → elige por índice, `0`/no-numérico/fuera-de-rango → `None`,
  shortlist vacío no llama al modelo, excepción → `None` + log warning.
- ETL openFDA: `normalize_openfda_effects` con normalizador falso → filas `LLM_NORMALIZED`
  (texto original en `source_id`), frases no mapeadas descartadas; `split_adverse_reactions`
  segmenta y deduplica; `enrichment` fusiona `openfda_effects.json` en `side_effects`.
- Logging: un fallo de mapeo ID→CID emite un registro `warning` (capturado con `caplog`).
- `pytest` verde, Tier 1 offline.

## Fuera de alcance

- El **motor de riesgo** en sí (consumidor de estos datos; proyecto siguiente).
- Normalización de las **diferencias estructurales** entre agencias (hoy solo CIMA).
- Extracción clínica (NER) real del blob openFDA: `split_adverse_reactions` es segmentación
  best-effort por delimitadores, no NER; el normalizador precision-first descarta el ruido.
- Descarga/fetch masivo de openFDA en el ETL: hoy el ETL codifica un `reactions.json`
  (CID → frases) ya preparado; el `fetch_openfda_label` on-demand (Tier 2) ya existe aparte.
- Otras agencias nacionales aparte de CIMA/AEMPS.
- I/O en modelos (se mantiene la regla: enriquecimiento en `enrichment`/`repository`).
```

