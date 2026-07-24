# FinSightAgent

FinSightAgent is an evidence-first financial event research system. The current implementation is the first deterministic vertical slice: ingest a disclosure, classify an event, resolve a security code, register evidence, create a source-qualified claim, and expose a fact card through FastAPI.

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

Run the Outbox publisher in a second process when PostgreSQL and Redis are available:

```bash
uv run python -m app.worker outbox
```

## Storage status

Default local development uses PostgreSQL (`FINSIGHT_REPOSITORY=postgresql`). `./scripts/start.sh --dev` starts Postgres + Redis via `deploy/docker-compose.dev-data.yml` (host ports `5432` and `6380`), runs `alembic upgrade head`, then launches the API and Vite admin. All domains—including LLM provider configs and agent bindings—persist in PostgreSQL across restarts.

Set `FINSIGHT_REPOSITORY=memory` only for disposable in-process runs (LLM configs still write to `.data/llm_config.json`). The complete vertical slice and its Outbox record are committed in one transaction under PostgreSQL.

## Source scheduling

Sources are synced manually from the Admin Sources page / `POST /api/v1/sources/{id}/sync`, or automatically by the source worker:

```bash
uv run python -m app.worker source
```

Each active source runs on its `crawl_interval_seconds` (minimum 60). The worker rescans the DB every 60 seconds so enable/disable and interval edits apply without restart. Compose includes a `source-worker` service. Recent attempts are stored in `platform.ingest_runs` and listed via `GET /api/v1/sources/{id}/runs`.

## Deployment baseline

Alembic is the only supported production schema initializer; the application never creates tables at startup. Before Compose deployment, supply a unique JWT secret of at least 32 characters:

```bash
export FINSIGHT_JWT_SECRET="replace-with-a-random-production-secret"
docker compose -f deploy/docker-compose.yml up --build
```

The API exposes `/health/live` for process liveness and `/health/ready` for database readiness. The container runs as the unprivileged `finsight` user. `report_versions` is still mutable for state transitions in this release; immutable replacement versions are intentionally deferred to the next delivery batch.

For a persistent deployment, provision the first operator after migrations:

```bash
FINSIGHT_REPOSITORY=postgresql uv run python -m app.admin admin --role admin
```

The command prompts for a password (or accepts `--password` for automation). Store `FINSIGHT_JWT_SECRET` in a secret manager outside development.

Local content-addressed Artifact storage, DocumentRevision, Redis Streams Outbox publishing, retry/dead-letter handling, and persistent Inbox deduplication are implemented. Event clustering, real exchange adapters, PDF/OCR parsing, and production object storage are not yet complete.

System and detailed design documents are indexed in [`docs/README.md`](docs/README.md).
