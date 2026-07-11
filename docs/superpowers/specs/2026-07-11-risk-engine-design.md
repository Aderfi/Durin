# Diseño: motor de riesgo farmacológico

**Fecha:** 2026-07-11
**Alcance:** el motor que evalúa el riesgo de un `Patient` a partir de su medicación
enriquecida y sus enfermedades, y produce alertas priorizadas para el clínico. Añade el
módulo `src/risk/`. **No** implementa las fuentes de datos de los ejes fármaco-enfermedad
ni age-modifier (sub-proyectos ETL propios), ni la exposición FastAPI.

> Convención: spec en español; **todos los docstrings y comentarios en inglés**. Español
> solo en locale JSON5.

## Contexto

La capa de enriquecimiento (spec `2026-07-11-pharmacovigilance-enrichment-design.md`) ya
puebla cada `Drug` con `side_effects` (SIDER) e `interactions` (TWOSIDES), cada hecho con
`Provenance` obligatoria. El motor de riesgo es el **consumidor** de esos datos. La regla
del proyecto se mantiene: *los modelos solo validan; la lógica (evaluación, I/O) vive
fuera*.

## Decisiones cerradas (brainstorming)

- **Salida y usuario:** para el **clínico**. Unidad funcional = **riesgo del paciente**
  (no por par de fármacos suelto).
- **Tres ejes de riesgo**, agregados a nivel paciente:
  1. Fármaco–efecto adverso (por fármaco). Datos: SIDER (ya disponibles).
  2. Fármaco–fármaco (pares dentro de la medicación). Datos: TWOSIDES (ya disponibles).
  3. Fármaco–enfermedad (cada fármaco × cada afección ICD10 del paciente). **Sin datos
     todavía** — se lee de una interfaz de store poblada por fases.
- **Scoring:** reglas deterministas → cada regla disparada = `Alert` con severidad y
  `Provenance`. Riesgo del paciente = **agregación transparente** (sin pesos opacos).
- **Agregación:** `tier` del paciente = severidad de la **peor** alerta; además una
  **carga acumulada** (conteo por severidad) para reflejar polimedicación.
- **Arquitectura (enfoque A):** un evaluador por eje + un `RiskEngine` que los orquesta.
- **Fármaco-enfermedad y age-modifier:** motor completo ahora contra **interfaces de
  store**; los datos se pueblan después (ETLs = sub-proyectos), patrón placeholder.
- **Moduladores de paciente disponibles sin cambiar el modelo:** `age` y `diseases`
  (ICD10). La insuficiencia renal, hepática, etc. son enfermedades ICD10 → las captura el
  eje fármaco-enfermedad; la edad va por el modulador Beers/STOPP.

## Disposición de módulos

```
src/risk/
  __init__.py
  models.py              # Alert, RiskBurden, RiskAssessment, RiskSeverity, RiskAxis
  severity.py            # map InteractionSeverity/SeverityLevel -> RiskSeverity
  stores.py              # DiseaseInteractionStore, AgeRiskStore (interfaces + placeholder)
  evaluators/
    __init__.py
    base.py              # Evaluator protocol
    adverse_effect.py    # AdverseEffectEvaluator
    drug_drug.py         # DrugDrugEvaluator
    drug_disease.py      # DrugDiseaseEvaluator
    age_modifier.py      # AgeModifierEvaluator
  engine.py              # RiskEngine.assess(patient) -> RiskAssessment
```

## Modelos (`src/risk/models.py`)

```python
RiskSeverity = Literal["low", "moderate", "high", "critical"]
RiskAxis = Literal["adverse_effect", "drug_drug", "drug_disease", "age_modifier"]
```

```python
class Alert(DomainModel):
    axis: RiskAxis
    severity: RiskSeverity
    drug_cids: list[int]              # 1 (AE/disease/age) or 2 (drug-drug)
    disease_icd10: str | None = None  # drug-disease axis
    title: NonEmptyStr                # e.g. "NSAID in renal insufficiency"
    detail: str | None = None
    recommendation: str | None = None
    provenance: Provenance            # mandatory (project invariant)

class RiskBurden(DomainModel):
    critical: int = 0
    high: int = 0
    moderate: int = 0
    low: int = 0

class RiskAssessment(DomainModel):
    patient_id: int
    tier: RiskSeverity                # severity of the worst alert ("low" if none)
    burden: RiskBurden                # count of alerts per severity
    alerts: list[Alert]
```

Invariantes:
- Toda `Alert` lleva `Provenance` (heredada del hecho: `SideEffect`/`Interaction`, o de la
  fuente drug-disease/age). Imposible una alerta sin fuente.
- `drug_cids` tiene 1 elemento salvo en el eje `drug_drug` (2, el par implicado).

## Mapeo de severidad (`src/risk/severity.py`)

Función pura `to_risk_severity(...)`, determinista:

- `InteractionSeverity`: `minor→low`, `moderate→moderate`, `major→high`,
  `contraindicated→critical`.
- `SeverityLevel` (efecto adverso): solo se emite alerta si `severe → high`. `mild`/
  `moderate`/`None` **no** generan alerta (evita inundar al clínico con efectos triviales).
- Fármaco-enfermedad: la severidad la aporta la fuente (contraindicación absoluta →
  `critical`; relativa/precaución → `high`).
- Age-modifier (Beers/STOPP): `moderate` o `high` según la regla.

## Evaluadores (`src/risk/evaluators/`)

Contrato común (`base.py`):

```python
class Evaluator(Protocol):
    def evaluate(self, patient: Patient) -> list[Alert]: ...
```

Cada evaluador se construye con los stores que necesita (inyección) y es puro respecto al
`Patient` (sin estado mutable). Detalle por eje:

### AdverseEffectEvaluator (`adverse_effect.py`)
- Recorre cada `Drug` de cada `Med` activo del paciente.
- Por cada `SideEffect` con `severity == "severe"` → `Alert(axis="adverse_effect",
  severity="high", drug_cids=[drug.cid], title=effect.name, provenance=effect.provenance)`.
- No red: consume el `Drug` ya enriquecido.

### DrugDrugEvaluator (`drug_drug.py`)
- Reúne los CIDs de todos los principios activos del paciente (todas las meds activas).
- Para cada `Drug`, recorre sus `interactions`; si `interaction.interacting_cid`
  corresponde a **otro fármaco presente en el paciente**, emite una `Alert(
  axis="drug_drug", drug_cids=[cid_a, cid_b],
  severity=to_risk_severity(interaction.severity), title=interaction.mechanism,
  provenance=interaction.provenance)`.
- Dedup por par no ordenado de CIDs + efecto (evita duplicar la interacción A-B y B-A).

**Cambio necesario en la capa de enriquecimiento** (mejora dirigida, código que tocamos):
`Interaction` no expone hoy el CID del fármaco interactuante — `interacting_drug` es solo
un nombre. Para emparejar pares por identidad química hace falta el CID:
- Añadir `interacting_cid: int | None = None` al modelo `Interaction`
  (`src/data/schemas/drug.py`).
- `enrichment.interactions()` lo puebla desde `raw["interacting_cid"]` (el dato ya existe
  en el dataset TWOSIDES, solo se estaba descartando).
- `require_drug_identity` sigue satisfecho por `interacting_drug` (nombre); `interacting_cid`
  es adicional, opcional (fuentes no-TWOSIDES pueden no traerlo).

### DrugDiseaseEvaluator (`drug_disease.py`)
- Para cada `Drug` × cada enfermedad ICD10 del paciente, consulta
  `DiseaseInteractionStore.lookup(cid, icd10) -> DiseaseInteraction | None`.
- Si hay contraindicación → `Alert(axis="drug_disease", drug_cids=[cid],
  disease_icd10=icd10, severity=..., title=..., provenance=...)`.
- El store es una **interfaz**; el placeholder devuelve `None` siempre (datos por fases).

### AgeModifierEvaluator (`age_modifier.py`)
- Para cada `Drug`, consulta `AgeRiskStore.rules_for(cid, age) -> list[AgeRule]`
  (reglas tipo Beers/STOPP: fármaco potencialmente inapropiado dado el rango de edad).
- Cada regla aplicable → `Alert(axis="age_modifier", drug_cids=[cid], severity=...,
  title=rule.description, provenance=rule.provenance)`.
- Store = interfaz + **semilla mínima** (unas pocas reglas Beers para probar el camino);
  el catálogo completo es fase posterior.

## Stores (`src/risk/stores.py`)

Interfaces (Protocol) + implementaciones placeholder:

```python
class DiseaseInteraction(DomainModel):
    cid: int
    icd10: str
    severity: RiskSeverity
    description: NonEmptyStr
    provenance: Provenance

class DiseaseInteractionStore(Protocol):
    def lookup(self, cid: int, icd10: str) -> DiseaseInteraction | None: ...

class EmptyDiseaseInteractionStore:
    """Placeholder until the drug-disease ETL exists; resolves nothing."""
    def lookup(self, cid: int, icd10: str) -> DiseaseInteraction | None:
        return None
```

```python
class AgeRule(DomainModel):
    cid: int
    min_age: int | None
    max_age: int | None
    severity: RiskSeverity
    description: NonEmptyStr
    provenance: Provenance

class AgeRiskStore(Protocol):
    def rules_for(self, cid: int, age: int) -> list[AgeRule]: ...

class SeedAgeRiskStore:
    """Minimal in-memory Beers-style seed; full catalog is a later phase."""
    ...
```

## Motor (`src/risk/engine.py`)

```python
class RiskEngine:
    def __init__(self, evaluators: list[Evaluator]) -> None: ...
    def assess(self, patient: Patient) -> RiskAssessment:
        # run each evaluator, collect + dedup alerts, aggregate
        ...
```

- Corre todos los evaluadores, concatena sus `Alert`s, dedup global (por
  `axis`+CIDs ordenados+título).
- `tier` = severidad máxima presente (orden low<moderate<high<critical); `"low"` si no hay
  alertas.
- `burden` = conteo de alertas por severidad.
- Devuelve `RiskAssessment`.
- Los `Drug` del paciente deben llegar **ya enriquecidos** (vía
  `repository.get_enriched_drug`); el motor no hace I/O de PubChem/farmacovigilancia.
- Un builder de conveniencia `default_engine(disease_store, age_store)` arma la lista
  estándar de evaluadores.

## Errores / logging

- Logging central (`src/utils/logging.py`); un evaluador que no encuentra datos devuelve
  lista vacía (ausencia ≠ error; nunca se fabrica una alerta).
- Fallos de un evaluador se registran y **no** tumban el resto (el motor aísla cada eje);
  se loguea y se continúa con los demás evaluadores.
- Toda `Alert` sin `provenance` → `ValidationError` (invariante del modelo).

## Testing / verificación

- `to_risk_severity`: unit tests de cada mapeo (incl. `severe→high`, `mild→` sin alerta).
- `AdverseEffectEvaluator`: paciente con un `Drug` con efecto `severe` → 1 alerta `high`;
  con efecto `mild` → 0 alertas.
- `Interaction.interacting_cid`: `enrichment.interactions()` lo puebla desde el dataset
  TWOSIDES; una `Interaction` construida a mano sin él → `interacting_cid is None`.
- `DrugDrugEvaluator`: paciente con dos fármacos que interactúan (match por
  `interacting_cid`) → 1 alerta (no duplicada); fármaco cuyo `interacting_cid` no está en el
  paciente → 0 alertas.
- `DrugDiseaseEvaluator`: con un `DiseaseInteractionStore` fake que devuelve una
  contraindicación → 1 alerta con `disease_icd10`; con `EmptyDiseaseInteractionStore` → 0.
- `AgeModifierEvaluator`: `SeedAgeRiskStore` con una regla → alerta si la edad cae en rango;
  fuera de rango → 0.
- `RiskEngine.assess`: agregación → `tier` = peor severidad; `burden` correcto; dedup;
  paciente sin riesgos → `tier="low"`, `alerts=[]`.
- `Alert` sin `provenance` → `ValidationError`.
- `pytest` verde, todo offline (stores fake/placeholder, `Drug` enriquecido con fixtures).

## Fuera de alcance

- **ETL de contraindicaciones fármaco-enfermedad** (MED-RT `CI_with`, openFDA
  `contraindications`) → sub-proyecto propio; aquí solo la interfaz + placeholder.
- **Catálogo completo age-modifier** (Beers/STOPP) → fase posterior; aquí interfaz +
  semilla mínima.
- Exposición **FastAPI** del motor.
- Scoring **ML/ponderado** (se eligió reglas + agregación transparente).
- Enriquecimiento de `Drug` (ya cubierto por la capa de farmacovigilancia).
