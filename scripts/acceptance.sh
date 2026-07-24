#!/usr/bin/env sh
set -eu

umask 077
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$#" -ne 0 ]; then
    echo "Usage: $0" >&2
    exit 2
fi

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

USERNAME=${FINSIGHT_ACCEPTANCE_USERNAME:-${FINSIGHT_BOOTSTRAP_ADMIN_USERNAME:-}}
PASSWORD=${FINSIGHT_ACCEPTANCE_PASSWORD:-${FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD:-}}
BASE_URL=${FINSIGHT_ACCEPTANCE_BASE_URL:-http://127.0.0.1:8000}
if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
    echo "Set FINSIGHT_ACCEPTANCE_USERNAME and FINSIGHT_ACCEPTANCE_PASSWORD." >&2
    exit 1
fi

for command in curl docker python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is missing: $command" >&2
        exit 1
    fi
done

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/finsight-acceptance.XXXXXX")
trap 'rm -rf -- "$TMP_DIR"' EXIT HUP INT TERM

curl -fsS "$BASE_URL/health/ready" >"$TMP_DIR/ready.json"
python3 - "$TMP_DIR/ready.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    assert json.load(response)["status"] == "ready"
PY

anonymous_status=$(
    curl -sS -o "$TMP_DIR/anonymous.json" -w '%{http_code}' \
        -H 'Content-Type: application/json' -d '{}' \
        "$BASE_URL/api/v1/documents/ingest"
)
if [ "$anonymous_status" != "401" ]; then
    echo "Anonymous ingestion returned HTTP $anonymous_status; expected 401." >&2
    exit 1
fi

python3 - "$USERNAME" "$PASSWORD" >"$TMP_DIR/login-request.json" <<'PY'
import json
import sys

json.dump({"username": sys.argv[1], "password": sys.argv[2]}, sys.stdout)
PY
login_status=$(
    curl -sS -o "$TMP_DIR/login-response.json" -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        --data-binary "@$TMP_DIR/login-request.json" \
        "$BASE_URL/api/v1/auth/login"
)
if [ "$login_status" != "200" ]; then
    echo "Login returned HTTP $login_status; expected 200." >&2
    exit 1
fi
python3 - "$TMP_DIR/login-response.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    token = json.load(response)["data"]["access_token"]
assert isinstance(token, str) and token
PY

compose() {
    docker compose -f deploy/docker-compose.yml "$@"
}

services="postgres redis api outbox-worker workflow-worker"
for service in $services; do
    container_id=$(compose ps --status running -q "$service")
    if [ -z "$container_id" ]; then
        echo "Compose service is not running: $service" >&2
        exit 1
    fi
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")
    if [ "$health" != "healthy" ]; then
        echo "Compose service is not healthy: $service ($health)" >&2
        exit 1
    fi
done

compose exec -T api sh -c '
    current=$(alembic current | awk "NR == 1 { print \$1 }")
    heads=$(alembic heads | awk "NR == 1 { print \$1 }")
    count=$(alembic heads | awk "END { print NR }")
    test "$count" -eq 1
    test -n "$current"
    test "$current" = "$heads"
'

echo "Acceptance passed: readiness, authentication, migrations, and service health."
