#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

PID_DIR="$ROOT/.data"
API_PID_FILE="$PID_DIR/api.pid"
WEB_PID_FILE="$PID_DIR/web.pid"
COMPOSE_FILE="deploy/docker-compose.yml"
DEV_DATA_COMPOSE_FILE="deploy/docker-compose.dev-data.yml"

usage() {
    cat <<EOF >&2
Usage: $0 [--dev | --dev-data | --purge]

  (default)   Stop Compose stack; keep PostgreSQL, Redis, and artifact volumes
  --dev       Stop local API + Vite processes started by start.sh --dev
  --dev-data  Stop local Postgres/Redis from docker-compose.dev-data.yml
  --purge     Stop Compose and remove all Docker volumes (destructive)
EOF
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
            sleep 1
            if is_pid_running "$pid"; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$pid_file"
    fi
}

stop_dev() {
    stop_pid_file "$WEB_PID_FILE"
    stop_pid_file "$API_PID_FILE"
    # Reload/watch children may outlive the pid-file parent.
    pkill -f "uvicorn app.main:app --reload" 2>/dev/null || true
    pkill -f "vite.*/Users/zhaozhengpin/Workspace/experiments/FinSightAgent/web" 2>/dev/null || true
    if command -v lsof >/dev/null 2>&1; then
        for port in 5173; do
            pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
            if [ -n "$pids" ]; then
                # shellcheck disable=SC2086
                kill $pids 2>/dev/null || true
            fi
        done
        # Free :8000 only when the listener is a local uvicorn (not docker-proxy).
        for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
            cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
            case "$cmd" in
            *uvicorn* | *python*)
                kill "$pid" 2>/dev/null || true
                ;;
            esac
        done
    fi
    echo "Local development processes stopped (Postgres/Redis left running)."
}

stop_dev_data() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required." >&2
        exit 1
    fi
    docker compose -f "$DEV_DATA_COMPOSE_FILE" down --timeout 30 --remove-orphans
    echo "Local PostgreSQL + Redis (dev-data) stopped; volumes retained."
}

stop_compose() {
    purge=${1:-}
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required for Compose mode." >&2
        exit 1
    fi

    if [ "$purge" = "--purge" ]; then
        docker compose -f "$COMPOSE_FILE" down --timeout 60 --remove-orphans --volumes
        echo "FinSightAgent stopped; PostgreSQL, Redis, and artifact volumes were removed."
    else
        docker compose -f "$COMPOSE_FILE" down --timeout 60 --remove-orphans
        echo "FinSightAgent stopped gracefully; PostgreSQL, Redis, and artifact volumes were retained."
    fi
}

case "${1:-}" in
-h | --help)
    usage
    exit 0
    ;;
--dev)
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi
    stop_dev
    ;;
--dev-data)
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi
    stop_dev_data
    ;;
--purge)
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi
    stop_compose --purge
    ;;
"")
    stop_compose
    ;;
*)
    usage
    exit 2
    ;;
esac
