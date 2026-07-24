#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$#" -ne 3 ] || [ "$2" != "--confirm" ] || [ "$3" != "RESTORE" ]; then
    echo "Usage: $0 BACKUP_DIR --confirm RESTORE" >&2
    echo "Restore replaces the current database and artifact contents." >&2
    exit 2
fi

BACKUP_DIR=$1
if [ ! -d "$BACKUP_DIR" ] || [ -L "$BACKUP_DIR" ]; then
    echo "Backup directory is invalid: $BACKUP_DIR" >&2
    exit 1
fi
BACKUP_DIR=$(CDPATH= cd -- "$BACKUP_DIR" && pwd)

for name in postgres.dump artifacts.tar.gz SHA256SUMS; do
    if [ ! -f "$BACKUP_DIR/$name" ] || [ -L "$BACKUP_DIR/$name" ] || [ ! -s "$BACKUP_DIR/$name" ]; then
        echo "Missing, empty, or unsafe backup input: $BACKUP_DIR/$name" >&2
        exit 1
    fi
done

if ! awk '
    NF != 2 { exit 1 }
    $2 == "postgres.dump" { dump++ }
    $2 == "artifacts.tar.gz" { artifacts++ }
    $2 != "postgres.dump" && $2 != "artifacts.tar.gz" { exit 1 }
    END { exit !(NR == 2 && dump == 1 && artifacts == 1) }
' "$BACKUP_DIR/SHA256SUMS"; then
    echo "SHA256SUMS must contain exactly the two expected backup files." >&2
    exit 1
fi

(
    cd "$BACKUP_DIR"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum -c SHA256SUMS
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 -c SHA256SUMS
    else
        echo "sha256sum or shasum is required." >&2
        exit 1
    fi
)

if ! tar -tzf "$BACKUP_DIR/artifacts.tar.gz" | awk '
    /^\// || /(^|\/)\.\.(\/|$)/ { bad=1 }
    END { exit bad }
'; then
    echo "Artifact archive is invalid or contains an unsafe path." >&2
    exit 1
fi

compose() {
    docker compose -f deploy/docker-compose.yml "$@"
}

if [ -z "$(compose ps --status running -q postgres)" ]; then
    echo "PostgreSQL service must be running before restore." >&2
    exit 1
fi
compose exec -T postgres pg_restore --list <"$BACKUP_DIR/postgres.dump" >/dev/null

stopped=0
restart_services() {
    if [ "$stopped" -eq 1 ]; then
        echo "Restore interrupted; attempting to restart services." >&2
        compose up -d >&2 || true
    fi
}
trap restart_services EXIT HUP INT TERM

compose stop --timeout 30 api outbox-worker workflow-worker
stopped=1

compose exec -T postgres sh -c \
    'exec pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    <"$BACKUP_DIR/postgres.dump"
compose run --rm --no-deps -T --entrypoint sh api -c \
    'find /data/artifacts -mindepth 1 -delete && exec tar -C /data/artifacts -xzf -' \
    <"$BACKUP_DIR/artifacts.tar.gz"

compose up -d
stopped=0
trap - EXIT HUP INT TERM
echo "Restore completed; run scripts/acceptance.sh before returning traffic."
