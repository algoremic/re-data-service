# re-data-service

CSV/NDJSON source of truth for DLD transactions. Serves market areas, PPSF, comps, sales mix, heatmap activity, and realized paths. Redis caches hot market reads.

**Compose name:** `data`  
**Host port:** `127.0.0.1:8001`  
**Entrypoint:** `uvicorn re_data.main:app --host 0.0.0.0 --port 8001`

## How to run

```powershell
cd re-platform
docker compose -f docker-compose.yml -f docker-compose.dev.yml --env-file .env up --build
```

Point `CSV_DATA_DIR` in `re-platform/.env` at the host folder that holds DLD dumps. Dev Compose mounts that folder read-only at `/data`.

```powershell
Invoke-WebRequest http://127.0.0.1:8001/health
```

## Local host venv (optional)

```powershell
cd re-data-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Environment

| Variable | Purpose |
|---|---|
| `BENCHMARKS_CSV_PATHS` | Semicolon-separated files inside `/data` — keep in sync with ML Compose |
| `REDIS_URL` | Stack Redis (`redis://redis:6379`) |
| `MARKET_CACHE_TTL_S` | Cache TTL (default 900) |
| `DATA_SERVICE_TOKEN` | Bearer for `POST /v1/admin/reload` (empty → 503) |

## HTTP API

| Method | Path | Auth |
|---|---|---|
| GET | `/health` | none |
| GET | `/v1/dataset` | none |
| GET | `/v1/market/areas` | none |
| GET | `/v1/market/ppsf` | none |
| GET | `/v1/market/index/latest` | none |
| GET | `/v1/market/comps` | none |
| GET | `/v1/market/realized-path` | none |
| GET | `/v1/market/stats/{area}` | none |
| GET | `/v1/market/sales-activity` | none |
| GET | `/v1/market/heatmap-activity` | none |
| POST | `/v1/admin/reload` | `DATA_SERVICE_TOKEN` |

## Low-level design (LLD)

```
Core / AI / MI  ----HTTP---->  data (8001)
                                 |-- DatasetStore (in-memory frames from CSV/NDJSON)
                                 |-- Redis market cache
                                 \-- /data volume (DLD dumps)
```

On startup the store loads `BENCHMARKS_CSV_PATHS`. Market routers query the store (and cache). `sales-activity` and `heatmap-activity` power dashboard / MI composition. Admin reload is fail-closed without a token and cooldown-protected.

| Path | Role |
|---|---|
| `src/re_data/routers/market.py` | Public market reads |
| `src/re_data/market/` | comps, sales_activity, heatmap_activity, realized_path |
| `src/re_data/store/` | Dataset ingest + snapshot |
| `src/re_data/routers/admin.py` | Token-gated reload |

## Branches

Feature → `Development` / `dev`. `master` is the default release branch.

## Engineering documentation

[Architecture, API contracts, operating manuals and review findings](docs/engineering-review/README.md) document the reviewed source snapshot and its known limitations.
