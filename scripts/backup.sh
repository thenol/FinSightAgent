#!/usr/bin/env sh
set -eu

umask 077
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [backup-root]" >&2
    exit 2
fi

BACKUP_ROOT=${1:-"$ROOT/backups"}
mkdir -p "$BACKUP_ROOT"
BACKUP_ROOT=$(CDPATH= cd -- "$BACKUP_ROOT" && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FINAL_DIR="$BACKUP_ROOT/finsight-$STAMP"
WORK_DIR="$BACKUP_ROOT/.finsight-$STAMP.tmp"

if [ -e "$FINAL_DIR" ] || [ -e "$WORK_DIR" ]; then
    echo "Backup destination already exists: $FINAL_DIR" >&2
    exit 1
fi

cleanup() {
    rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT HUP INT TERM
mkdir "$WORK_DIR"

compose() {
    docker compose -f deploy/docker-compose.yml "$@"
}

for service in postgres api; do
    if [ -z "$(compose ps --status running -q "$service")" ]; then
        echo "Required service is not running: $service" >&2
        exit 1
    fi
done

compose exec -T postgres sh -c \
    'exec pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    >"$WORK_DIR/postgres.dump"
compose exec -T api tar -C /data/artifacts -czf - . >"$WORK_DIR/artifacts.tar.gz"

if [ ! -s "$WORK_DIR/postgres.dump" ] || [ ! -s "$WORK_DIR/artifacts.tar.gz" ]; then
    echo "Backup produced an empty file." >&2
    exit 1
fi

(
    cd "$WORK_DIR"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum postgres.dump artifacts.tar.gz >SHA256SUMS
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 postgres.dump artifacts.tar.gz >SHA256SUMS
    else
        echo "sha256sum or shasum is required." >&2
        exit 1
    fi
)

mv "$WORK_DIR" "$FINAL_DIR"
trap - EXIT HUP INT TERM
echo "Backup created: $FINAL_DIR"
