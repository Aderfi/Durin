# Durin

> ⚠️ **Early development.** APIs, data models, and scope are still moving. Not ready for clinical or production use.

Durin is a research project aimed at building software to support the care of **polymedicated geriatric patients** — people on multiple concurrent medications, where the combined risk of adverse effects and drug–drug interactions grows sharply with each added drug.

The goal is a tool that ingests a patient's medication list and clinical profile, then surfaces the **risks worth a clinician's attention**: dangerous interactions, dosing concerns, and cumulative side-effect burden. AI-assisted analysis is on the roadmap to help prioritize and explain these risks, but the current focus is a solid, well-typed data foundation.

## Why

Polypharmacy in older adults is a leading, largely preventable source of hospitalization. The number of possible interactions scales combinatorially with medication count, and much of the relevant knowledge is scattered across references that are hard to apply quickly at the bedside. Durin is an attempt to make that knowledge structured, queryable, and actionable.

## Current state

What exists today is groundwork, not the finished tool:

- **Domain models** (`pydantic`) for drugs and patients, with validation — ATC codes, dosages, side effects, interactions, and patient medication profiles.
- **ATC/DDD catalog** built by a scraper (`scripts/atc_scraper.py`) into a local code lookup, mapping drugs to their anatomical/therapeutic/pharmacological classes.
- **Internationalization** via JSON5 locale catalogs (Spanish / English).
- Project scaffolding for a **FastAPI** service layer.

Not yet built: the interaction/risk engine, the AI layer, and any user-facing interface.

## Stack

Python 3.14 · Pydantic v2 · FastAPI · BeautifulSoup (scraping) · polars / scikit-learn (analysis, planned)

## Layout

```
src/
  data/        # schemas, validation, ATC catalog
  locales/     # es / en JSON5 catalogs + loader
  features/    # (planned) risk & interaction logic
  models/      # (planned) AI / ML models
scripts/       # ATC/DDD scraper
```

## Status & disclaimer

This is a work in progress and **not a medical device**. It provides no clinical advice and must not be used to make treatment decisions. All outputs are experimental and unvalidated.
