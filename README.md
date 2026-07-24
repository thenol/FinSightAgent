# FinSightAgent

FinSightAgent is an evidence-first research system for **listed-company announcement-style events**. It turns multi-source disclosures into event cards with source-qualified claims, then optionally runs a LangGraph research workflow (Fact Checker → Company Analyst → Skeptic → Synthesizer) with review, reports, and daily briefs.

MVP focus is five event types (`earnings_guidance`, `major_contract`, `merger_acquisition`, `shareholder_reduction`, `regulatory_penalty`). Ingestion keeps every document; cross-source “same story” alignment happens at the **Event** layer (matcher + router), not by byte-identical article dedup.

Current slice includes:

- RSS ingest with scheduling, HTML/PDF text extraction, and content-addressed revisions
- Rule classification + Event Router gate, entity/code resolution, and event clustering hooks
- Evidence / claim fingerprints, conflict detection, and fact cards
- Agent workflow with budgets, retries, tool gateway `as_of` guards, and Admin SPA
- JWT roles, Outbox → Redis Streams, and Alembic-managed PostgreSQL

Progress and backlog live in [`docs/07-work-progress.md`](docs/07-work-progress.md) and [`docs/08-improvement-backlog.md`](docs/08-improvement-backlog.md). Full design index: [`docs/README.md`](docs/README.md).

## Run locally

```bash
uv sync --extra dev --extra postgres
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the API console.

### Admin frontend (Vite + React)

Development (API on `:8000`, Vite proxy on `:5173`):

```bash
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/admin/`.

Production-style serving from FastAPI (`/admin` hosts the built SPA):

```bash
cd web && npm ci && npm run build
uv run uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/admin`.

In `development` only, a local bootstrap account is created automatically:
`admin` / `admin123`. Production deployments never create this account.

For Docker-based local operation, copy `.env.example` to `.env`, set a secure
`FINSIGHT_JWT_SECRET`, then run:

```bash
./scripts/start.sh --dev   # local API :8000 + Vite admin :5173
./scripts/stop.sh --dev    # stop local API + Vite

./scripts/start.sh         # Docker Compose (API hosts built SPA at /admin/)
./scripts/start.sh --build # rebuild images (includes web/ SPA) and start Compose
./scripts/stop.sh          # preserve PostgreSQL, Redis, and artifacts
./scripts/stop.sh --purge  # remove all Docker volumes as well
```

The example `.env` also bootstraps a local admin account on first startup:
`admin` / `admin123`. Remove the `FINSIGHT_BOOTSTRAP_ADMIN_*` variables before
deploying to a shared or production environment.

## Test and lint

```bash
uv run pytest
uv run ruff check .
cd web && npm test && npm run lint && npm run build
```

Workers (PostgreSQL + Redis required for Outbox / scheduling):

```bash
uv run python -m app.worker outbox   # publish transactional Outbox to Redis Streams
uv run python -m app.worker source   # poll active sources on crawl_interval_seconds
```

## Storage status

Default local development uses PostgreSQL (`FINSIGHT_REPOSITORY=postgresql`). `./scripts/start.sh --dev` starts Postgres + Redis via `deploy/docker-compose.dev-data.yml` (host ports `5432` and `6380`), runs `alembic upgrade head`, then launches the API and Vite admin. All domains—including LLM provider configs and agent bindings—persist in PostgreSQL across restarts.

Set `FINSIGHT_REPOSITORY=memory` only for disposable in-process runs (LLM configs still write to `.data/llm_config.json`). The ingest/research pipeline and its Outbox record are committed in one transaction under PostgreSQL.

## Source scheduling

Sources are synced manually from the Admin Sources page / `POST /api/v1/sources/{id}/sync`, or automatically by the source worker above.

Each active source runs on its `crawl_interval_seconds` (minimum 60). The worker rescans the DB every 60 seconds so enable/disable and interval edits apply without restart. Compose includes a `source-worker` service. Recent attempts are stored in `platform.ingest_runs` and listed via `GET /api/v1/sources/{id}/runs`.

## Deployment baseline

Alembic is the only supported production schema initializer; the application never creates tables at startup. Before Compose deployment, supply a unique JWT secret of at least 32 characters:

```bash
export FINSIGHT_JWT_SECRET="replace-with-a-random-production-secret"
docker compose -f deploy/docker-compose.yml up --build
```

The API exposes `/health/live` for process liveness and `/health/ready` for database readiness. The container runs as the unprivileged `finsight` user. `report_versions` is still mutable for state transitions in this release; immutable replacement versions are intentionally deferred.

For a persistent deployment, provision the first operator after migrations:

```bash
FINSIGHT_REPOSITORY=postgresql uv run python -m app.admin admin --role admin
```

The command prompts for a password (or accepts `--password` for automation). Store `FINSIGHT_JWT_SECRET` in a secret manager outside development.

### Implemented vs still open

| Area | Status |
| --- | --- |
| Artifacts, DocumentRevision, Outbox → Redis Streams, Inbox dedup | Implemented |
| Event matcher / router, claim fingerprints, Agent graph + Admin SPA | Implemented (thresholds / merge UX still maturing) |
| Text PDF page/offset evidence | Partial (tables, OCR, exchange S-tier APIs still open) |
| Production object storage, live market acceptance | Not complete |

See [`docs/07-work-progress.md`](docs/07-work-progress.md) for the authoritative checklist.
