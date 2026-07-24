#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

PID_DIR="$ROOT/.data"
API_PID_FILE="$PID_DIR/api.pid"
WEB_PID_FILE="$PID_DIR/web.pid"
API_LOG="$PID_DIR/api.log"
WEB_LOG="$PID_DIR/web.log"
COMPOSE_FILE="deploy/docker-compose.yml"
DEV_DATA_COMPOSE_FILE="deploy/docker-compose.dev-data.yml"

usage() {
    cat <<EOF >&2
Usage: $0 [--build | --dev]

  (default)  Start the Docker Compose stack (API hosts built SPA at /admin)
  --build    Rebuild images (includes web/ SPA) and start Compose
  --dev      Local development: API :8000 + Vite admin :5173
             With FINSIGHT_REPOSITORY=postgresql, also starts Postgres/Redis
             via $DEV_DATA_COMPOSE_FILE and runs alembic migrations.
EOF
}

load_env() {
    if [ -f .env ]; then
        set -a
        # shellcheck disable=SC1091
        . ./.env
        set +a
    fi
}

require_jwt_secret() {
    if [ -z "${FINSIGHT_JWT_SECRET:-}" ] || [ "${FINSIGHT_JWT_SECRET:-}" = "replace-with-a-random-32-byte-secret" ]; then
        echo "Set FINSIGHT_JWT_SECRET in .env or the environment before starting." >&2
        exit 1
    fi
    if [ "${#FINSIGHT_JWT_SECRET}" -lt 32 ]; then
        echo "FINSIGHT_JWT_SECRET must contain at least 32 characters." >&2
        exit 1
    fi
}

require_compose_env() {
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
        echo "Set POSTGRES_PASSWORD in .env or the environment before starting." >&2
        exit 1
    fi
    if [ -z "${FINSIGHT_COMPOSE_DATABASE_URL:-}" ]; then
        echo "Set FINSIGHT_COMPOSE_DATABASE_URL to the PostgreSQL URL used inside Compose." >&2
        exit 1
    fi
}

wait_http() {
    url=$1
    label=$2
    attempts=${3:-30}
    attempt=0
    until curl -fsS "$url" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge "$attempts" ]; then
            echo "$label did not become ready: $url" >&2
            return 1
        fi
        sleep 1
    done
}

is_pid_running() {
    pid=$1
    kill -0 "$pid" 2>/dev/null
}

stop_pid_file() {
    pid_file=$1
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if [ -n "$pid" ] && is_pid_running "$pid"; then
            kill "$pid" 2>/dev/null || true
            # Give the process group a moment, then force if needed.
            sleep 1
            if is_pid_running "$pid"; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$pid_file"
    fi
}

start_compose() {
    build_flag=${1:-}
    require_compose_env

    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required for Compose mode." >&2
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" config --quiet

    if [ "$build_flag" = "--build" ]; then
        docker compose -f "$COMPOSE_FILE" up --build -d
    else
        docker compose -f "$COMPOSE_FILE" up -d
    fi

    if ! wait_http "http://127.0.0.1:8000/health/ready" "Compose API" 60; then
        echo "Inspect logs with: docker compose -f $COMPOSE_FILE logs" >&2
        exit 1
    fi

    echo "FinSightAgent is ready."
    echo "  API docs:  http://127.0.0.1:8000/docs"
    echo "  Admin SPA: http://127.0.0.1:8000/admin/"
    echo "Run scripts/acceptance.sh to verify the deployment baseline."
}

port_in_use() {
    port=$1
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    else
        return 1
    fi
}

ensure_dev_data_plane() {
    if [ "${FINSIGHT_REPOSITORY:-memory}" != "postgresql" ]; then
        echo "FINSIGHT_REPOSITORY=${FINSIGHT_REPOSITORY:-memory} (in-process; LLM configs use .data/llm_config.json)."
        return 0
    fi

    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required when FINSIGHT_REPOSITORY=postgresql." >&2
        exit 1
    fi
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
        echo "Set POSTGRES_PASSWORD in .env before starting PostgreSQL." >&2
        exit 1
    fi

    echo "Starting local PostgreSQL + Redis ($DEV_DATA_COMPOSE_FILE)…"
    docker compose -f "$DEV_DATA_COMPOSE_FILE" up -d

    echo "Waiting for PostgreSQL health…"
    attempt=0
    until docker compose -f "$DEV_DATA_COMPOSE_FILE" exec -T postgres \
        pg_isready -U "${POSTGRES_USER:-finsight}" -d "${POSTGRES_DB:-finsight}" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "PostgreSQL did not become ready." >&2
            exit 1
        fi
        sleep 1
    done

    echo "Applying database migrations…"
    uv sync --extra postgres --quiet
    uv run alembic upgrade head
}

free_dev_ports() {
    # Full Compose binds :8000; local --dev needs the same port for the API.
    if port_in_use 8000; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '^(deploy-api-1|.*-api-1)$'; then
            echo "Port 8000 is held by Compose API; stopping deploy stack API services…"
            docker compose -f "$COMPOSE_FILE" stop api outbox-worker workflow-worker 2>/dev/null || true
            docker stop deploy-api-1 2>/dev/null || true
            sleep 1
        fi
    fi
    if port_in_use 8000 && command -v lsof >/dev/null 2>&1; then
        for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
            cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
            case "$cmd" in
            *uvicorn* | *python*)
                echo "Stopping local process on :8000 (pid $pid)…"
                kill "$pid" 2>/dev/null || true
                sleep 1
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
                ;;
            esac
        done
        sleep 1
    fi
    if port_in_use 8000; then
        echo "Port 8000 is still in use. Stop the other process, then retry." >&2
        exit 1
    fi
}

start_dev() {
    require_jwt_secret

    if ! command -v uv >/dev/null 2>&1; then
        echo "uv is required for --dev mode." >&2
        exit 1
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "npm is required for --dev mode." >&2
        exit 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "curl is required." >&2
        exit 1
    fi

    mkdir -p "$PID_DIR"

    if [ -f "$API_PID_FILE" ] || [ -f "$WEB_PID_FILE" ]; then
        echo "Existing local processes detected; stopping them first."
        stop_pid_file "$WEB_PID_FILE"
        stop_pid_file "$API_PID_FILE"
    fi

    ensure_dev_data_plane
    free_dev_ports

    if [ ! -d web/node_modules ]; then
        echo "Installing frontend dependencies (npm ci)…"
        npm ci --prefix web
    fi

    echo "Starting API on :8000 (repository=${FINSIGHT_REPOSITORY:-memory})…"
    (
        cd "$ROOT"
        # Detach from the starter shell so Cursor/tool shells do not reap the API.
        # Reload only app/ so edits under deploy/, web/, .data/ do not bounce the server.
        setsid nohup uv run uvicorn app.main:app --reload --reload-dir app \
            --host 127.0.0.1 --port 8000 \
            >"$API_LOG" 2>&1 </dev/null &
        echo $! >"$API_PID_FILE"
    )

    echo "Starting Vite admin on :5173…"
    (
        cd "$ROOT/web"
        setsid nohup npm run dev -- --host 127.0.0.1 --port 5173 \
            >"$WEB_LOG" 2>&1 </dev/null &
        echo $! >"$WEB_PID_FILE"
    )

    if ! wait_http "http://127.0.0.1:8000/health/live" "API" 45; then
        echo "API failed to start; see $API_LOG" >&2
        stop_pid_file "$WEB_PID_FILE"
        stop_pid_file "$API_PID_FILE"
        exit 1
    fi

    if ! wait_http "http://127.0.0.1:5173/admin/" "Vite admin" 45; then
        echo "Vite failed to start; see $WEB_LOG" >&2
        stop_pid_file "$WEB_PID_FILE"
        stop_pid_file "$API_PID_FILE"
        exit 1
    fi

    echo "FinSightAgent local development is ready."
    echo "  API docs:     http://127.0.0.1:8000/docs"
    echo "  Admin (Vite): http://127.0.0.1:5173/admin/"
    echo "  Repository:   ${FINSIGHT_REPOSITORY:-memory}"
    if [ "${FINSIGHT_REPOSITORY:-memory}" = "postgresql" ]; then
        echo "  Database:     ${FINSIGHT_DATABASE_URL:-}"
        echo "  Redis:        ${FINSIGHT_REDIS_URL:-}"
    fi
    echo "  Logs:         $API_LOG , $WEB_LOG"
    echo "Stop with:      ./scripts/stop.sh --dev"
    echo "  (Postgres/Redis keep running; use ./scripts/stop.sh --dev-data to stop them)"
}

case "${1:-}" in
-h | --help)
    usage
    exit 0
    ;;
--build)
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi
    load_env
    require_jwt_secret
    start_compose --build
    ;;
--dev)
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi
    load_env
    start_dev
    ;;
"")
    load_env
    require_jwt_secret
    start_compose
    ;;
*)
    usage
    exit 2
    ;;
esac
