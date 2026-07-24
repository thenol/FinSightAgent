# Repository Guidelines

## Project Structure & Module Organization

The repository contains a FastAPI vertical slice and design documentation in `docs/`:

- `docs/financial_news_multi_agent_system.md`: system vision and architecture.
- `docs/00-product-requirements.md`: product scope, roles, and user journeys.
- `docs/01-functional-architecture.md`: functional boundaries and module ownership.
- `docs/02-data-model.md`: domain entities, states, and storage rules.
- `docs/03-workflow-design.md`: event workflow and Agent orchestration.
- `docs/04-engineering-design.md`: APIs, runtime, security, and operations.
- `docs/05-mvp-acceptance.md`: MVP scope and acceptance criteria.
- `docs/06-architecture-decisions.md`: design decisions.
- `docs/design/`: component, contract, and storage designs.
- `docs/README.md`: documentation index and maintenance rules.

Application domains live under `app/`; tests, SQL migrations, and containers live in `tests/`, `migrations/`, and `deploy/`. Keep logic grouped by domain, not Agent.

## Build, Test, and Development Commands

Use:

```bash
uv sync --extra dev --extra postgres  # install all current dependencies
uv run alembic upgrade head     # apply database migrations
uv run uvicorn app.main:app --reload  # run the API locally
uv run pytest                   # run the test suite
uv run ruff check .             # lint Python code
```

Use `docker compose -f deploy/docker-compose.yml up --build` to start the API with PostgreSQL and Redis. Run `uv run python -m app.worker outbox` (or the Compose `outbox-worker` service) to publish transactional Outbox records to Redis Streams.

## Coding Style & Naming Conventions

Use UTF-8, LF line endings, and four spaces for Python indentation. Prefer `snake_case` for Python modules/functions, `PascalCase` for classes and Pydantic models, and lowercase kebab-case for new documentation files. Keep API paths versioned under `/api/v1/`. Agent and tool inputs must use explicit, versioned schemas; do not pass unstructured dictionaries across module boundaries.

For Markdown, use descriptive headings, fenced code blocks with language tags, and relative links. Keep detailed rules in their owning document instead of duplicating them.

## Testing Guidelines

Use `pytest`; name files `tests/test_<module>.py` and tests `test_<behavior>()`. Cover state transitions, schema validation, idempotency, recovery, permission checks, and time-bounded replay. Financial evaluations must respect each event's `as_of` timestamp to prevent future-data leakage.

## Commit & Pull Request Guidelines

Use concise Conventional Commit subjects such as `docs: clarify event lifecycle` or `feat: add filing ingestion`.

Pull requests should describe scope, affected modules, validation performed, and any schema or architecture decisions. Link relevant issues; include screenshots for UI changes and sample payloads for API changes. Never commit credentials, licensed source content, or generated runtime data.
