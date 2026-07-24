import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.yml"


def _compose_config() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    environment = os.environ.copy()
    environment.update(
        {
            "FINSIGHT_JWT_SECRET": "acceptance-test-secret-at-least-32-characters",
            "FINSIGHT_COMPOSE_DATABASE_URL": (
                "postgresql+psycopg://finsight:test-password@postgres:5432/finsight"
            ),
            "POSTGRES_PASSWORD": "test-password",
        }
    )
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_compose_services_have_recovery_baseline() -> None:
    config = _compose_config()
    services = config["services"]
    expected = {"api", "outbox-worker", "workflow-worker", "postgres", "redis"}
    assert expected <= services.keys()

    for name in expected:
        service = services[name]
        assert service["restart"] == "unless-stopped"
        assert service["healthcheck"]["test"]
        assert service["stop_grace_period"]

    assert any(volume["source"] == "postgres-data" for volume in services["postgres"]["volumes"])
    assert any(volume["source"] == "redis-data" for volume in services["redis"]["volumes"])
    assert any(volume["source"] == "artifacts" for volume in services["api"]["volumes"])
    assert "--appendonly" in services["redis"]["command"]
    assert "yes" in services["redis"]["command"]
    assert {"postgres-data", "redis-data", "artifacts"} <= config["volumes"].keys()


def test_compose_does_not_embed_database_password() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD: finsight" not in compose
    assert "${POSTGRES_PASSWORD:?" in compose
    assert "${FINSIGHT_COMPOSE_DATABASE_URL:?" in compose


def test_operational_scripts_contain_required_safety_guards() -> None:
    backup = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore.sh").read_text(encoding="utf-8")
    acceptance = (ROOT / "scripts" / "acceptance.sh").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop.sh").read_text(encoding="utf-8")

    assert "pg_dump" in backup
    assert "artifacts.tar.gz" in backup
    assert "SHA256SUMS" in backup
    assert "--confirm" in restore and "RESTORE" in restore
    assert "pg_restore --list" in restore
    assert "SHA256SUMS" in restore
    assert "POSTGRES_PASSWORD=" not in backup + restore
    assert "/health/ready" in acceptance
    assert "/api/v1/documents/ingest" in acceptance
    assert "/api/v1/auth/login" in acceptance
    assert "alembic current" in acceptance and "alembic heads" in acceptance
    assert "docker inspect" in acceptance
    assert "--purge" in stop
    assert 'down --timeout 60 --remove-orphans\n' in stop
    assert stop.count("--volumes") == 1


def test_runbook_covers_recovery_procedures_and_limitations() -> None:
    runbook = (ROOT / "docs" / "09-operations-runbook.md").read_text(encoding="utf-8")
    for topic in (
        "启动、验收与停止",
        "备份",
        "恢复",
        "Worker 中断恢复",
        "应用回滚",
        "密钥轮换",
        "Outbox 死信处理",
        "PITR",
        "灾备",
        "目标生产环境",
    ):
        assert topic in runbook


def test_image_declares_graceful_stop_signal() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "STOPSIGNAL SIGTERM" in dockerfile
