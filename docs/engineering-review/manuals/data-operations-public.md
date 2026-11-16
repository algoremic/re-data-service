# Data service operating manual

Public-repository edition, based only on the re-data-service Development source at `7c44244a67657f7ca72b3afd87859984b4650f68`, reviewed 2026-11-16. See the [Data LLD](../services/data-lld.md) and [OpenAPI](../contracts/data-openapi.json). No production operation was performed.

## Local data setup

Use Python3.12+ in separate venvs for normal development. Start from the branch/commit named in the release manifest. Default master data lacks MI's routes and protected reload; see branch-gap finding.

```bash
cd /path/to/re-data-service
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
export BENCHMARKS_CSV_PATHS='/absolute/data/Transactions.csv;/absolute/data/transactions-2026-02-23.csv'
export INDEX_CSV_PATH='/absolute/data/Residential_Sale_Index.csv'
export DLD_AREA_UNIT='sqm'
export PPSF_MIN='100'
export PPSF_MAX='20000'
export REDIS_URL=''
export APP_ENV='local'
.venv/bin/python -m uvicorn re_data.main:app --host 127.0.0.1 --port 8001
```

Use `INDEX_CSV_PATH=''` only when intentionally disabling the index input; otherwise a missing configured index makes readiness degraded. Semicolons separate transaction paths. A `.json` input is line-delimited JSON objects with DLD field names, not a JSON array; `.ndjson` extension currently follows CSV parsing. Read source schemas in `ingest/csv_transactions.py` before accepting a new vendor format. Generic comps use square feet, DLD/recent use configured units. Publish complete files atomically into a read-only mounted directory; do not edit a file while it is being hashed/parsed. No scheduler is built in.

Do not copy committed environment contents blindly. `.env` is loaded by the application; explicitly supply known configuration and secrets. In tests use `PYTHON_DOTENV_DISABLED=1` plus explicit settings/environment as needed, and run in a directory without unreviewed `.env` files. This disables python-dotenv auto-load but Pydantic Settings can still read its configured `.env`; fixture/code settings must isolate that too.

| Data config | Default / meaning |
|---|---|
| BENCHMARKS_CSV_PATHS | empty; semicolon-delimited files |
| INDEX_CSV_PATH | `/data/Residential_Sale_Index.csv`; empty disables |
| DLD_AREA_UNIT | sqm; only sqft bypasses conversion; validate explicit values operationally |
| PPSF_MIN/PPSF_MAX |100/20000 AED/sqft; no mathematical assurance these fit every segment |
| REDIS_URL | empty disables cache; no hard dependency for correct uncached responses |
| MARKET_CACHE_TTL_S |900 seconds |
| DATA_SERVICE_TOKEN | empty disables administrative reload via503; configured value≤512 chars |
| MAX_RELOAD_S |300 seconds, must be positive; await timeout does not stop a running thread |
| ADMIN_RELOAD_COOLDOWN_S |5 seconds; zero disables; per-process |
| APP_ENV | local; root/health metadata only, data admin remains fail-closed regardless |
| PORT |8001 in Settings; uvicorn CLI/container command determines actual listener |
| MONGO_URI/MONGO_DB/collection names | declared but not persistent storage for this ingest/read path |


## Verify, refresh and recover

Use GET `/health` and `/v1/dataset` to inspect configured snapshot state, accepted/rejected rows, source hashes and latest actual transaction date. Loaded or complete does not prove fresh, semantically valid data. Verify known PPSF/stats/comparable examples, explicit square-foot/metre units, negative/nonfinite rejection and index numeric values.

`POST /v1/admin/reload` requires the configured DATA_SERVICE_TOKEN in a Bearer header in every environment; blank configuration yields503, missing header401 and wrong token403. The lock/cooldown are per process. A thread await timeout does not stop ingestion work already running. Preserve the prior immutable source and observed metadata before refresh, publish complete source files atomically, reload one instance, validate accepted rows and semantic examples, then coordinate replicas. See the LLD for exact error envelopes.

Keep approved prior source versions and parser/config identity for rollback. Restore sources/configuration and reload/restart deliberately; do not assume a cache flush is a data rollback. No backup/restore drill was performed.

## Tests and future changes

Run the existing suite from an isolated environment with controlled settings and no real services: `python -m pytest tests`. The reviewed Development suite had261 passing tests; targeted review probes additionally found negative-sign corruption, ordering/version/readiness issues. A pass count is not a correctness guarantee.

Parser, filter, unit or schema changes need CSV/string-JSON/numeric-JSON parity, signed finite numeric validation, explicit rejection counts and versioned semantic dataset/cache identity. API changes require generated-schema and consumer compatibility checks. Benchmark memory/latency at expected dataset sizes before increasing workers; each process loads its own dataset and reload staging adds memory. Document current source revision, input fingerprints, formula definitions, error semantics, known limitations and recovery verification whenever behavior changes.
