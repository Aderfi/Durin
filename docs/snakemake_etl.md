# Orchestrating the ETL with Snakemake

The offline pipeline (ATC scraper, ICD-10 scraper, rxnorm mapping, the
pharmacovigilance CSV build) used to be a set of scripts you ran by hand, in
the right order, with the right paths. A single `Snakefile` at the repo root
now wires them together.

## Scope: up to the CSVs, not past them

The DAG stops at `tmp/neo4j_import/*.csv`. It does not run
`neo4j-admin database import`, and it does not run `recover_chembl_cids.py`.
Both stay manual, on purpose — the Neo4j migration doc already explains why
the import step is deliberately not automated (it needs the server stopped,
needs `sudo`, and overwrites the only database Community edition has). This
Snakefile respects that decision rather than reopening it. `recover_chembl_cids.py`
is out of scope for a different reason: it writes into an already-imported,
live database, which is a different precondition than every other rule here
(files in, files out).

## Rules

```
scrape_atc_catalog        → src/data/atc/codes.json
scrape_icd10               → scripts/icd_codes.json
flatten_icd10               → scripts/plain_icd_codes.json
download_sider              → tmp/raw/meddra_all_se.tsv.gz
build_rxnorm_map            → tmp/rxnorm_to_cid.tsv
build_pharmacovigilance_csvs → tmp/neo4j_import/*.csv (6 files)
```

Each rule just wraps the CLI a script already had — `atc_scraper.py`,
`icd10_scraper.py`, `build_rxnorm_map.py`, and `build_pharmacovigilance.py`
all already took `--output`/`--out` style arguments, so wiring them into
Snakemake `input:`/`output:` declarations didn't need touching their
internals.

## What's fetched automatically vs. what isn't

Of the four raw sources the pharmacovigilance build needs, only SIDER has a
stable, direct file URL
(`https://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz`) — that's
the one `download_sider` fetches with `curl`. TWOSIDES, ChEMBL MoA, and
UniChem's crosswalk are portal exports with no fixed file URL to point a rule
at, so they're declared as plain paths in `tmp/` with no rule producing them.
If they're missing, Snakemake's own `MissingInputException` names the exact
file — there's no custom error message layered on top of that, it wasn't
needed.

The SIDER `.gz` is passed straight through to `build_pharmacovigilance.py`
without a separate decompression step — Polars' `pl.read_csv` decompresses
gzip based on the file extension on its own, which `parse_sider()` already
relies on.

## One script needed a small change

`scripts/json_plain.py` (the ICD-10 flattener) used to read from and write to
hardcoded relative paths (`icd_codes.json`, `plain_icd_codes.json`) with no
CLI. That doesn't fit a Snakemake rule, which needs to declare its
input/output paths explicitly. It now takes `--input`/`--output`, same
pattern as the other scripts, with the same default paths it used before —
verified byte-for-byte identical output against the old hardcoded version
before and after the change.

## Running it

```bash
uv run snakemake -n          # dry run — check what would run
uv run snakemake --cores 1   # actually run it
```

`snakemake` is a dev-only dependency (`uv sync` pulls it as part of the `dev`
group); it isn't wired into Docker. The ETL needs network access for
scraping and downloads, and there was no reason to run it inside a container
for now — `uv run snakemake` on the host is what's actually used.

Everything the DAG needs was already sitting in `tmp/` from a prior manual
run when this was built, which made it possible to validate the whole thing
(`snakemake -n`) against real data without waiting on any network calls.
