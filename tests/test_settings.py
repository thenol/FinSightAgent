import pytest

from app.platform.settings import Settings


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_JWT_SECRET_REQUIRED"):
        Settings(
            environment="production",
            repository="postgresql",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root="/data/artifacts",
            jwt_secret="development-only-secret-change-me-32-bytes",
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            settings_fernet_key="f" * 44,
        ).validate()


def test_production_requires_settings_fernet_key() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_SETTINGS_FERNET_KEY_REQUIRED"):
        Settings(
            environment="production",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root="/data/artifacts",
            jwt_secret="a" * 32,
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            settings_fernet_key="",
        ).validate()


def test_production_fernet_must_differ_from_jwt() -> None:
    secret = "a" * 44
    with pytest.raises(ValueError, match="FINSIGHT_SETTINGS_FERNET_KEY_MUST_DIFFER_FROM_JWT"):
        Settings(
            environment="production",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root="/data/artifacts",
            jwt_secret=secret,
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            settings_fernet_key=secret,
        ).validate()


def test_production_requires_absolute_artifact_root() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_ARTIFACT_ROOT_MUST_BE_ABSOLUTE"):
        Settings(
            environment="production",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root=".data/artifacts",
            jwt_secret="a" * 32,
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            settings_fernet_key="f" * 44,
        ).validate()


def test_bootstrap_admin_requires_password_pair() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_BOOTSTRAP_ADMIN_CREDENTIALS_REQUIRED"):
        Settings(
            environment="development",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root=".data/artifacts",
            jwt_secret="a" * 32,
            bootstrap_admin_username="admin",
            bootstrap_admin_password="",
        ).validate()


def test_document_purge_min_age_seconds_bounds() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_DOCUMENT_PURGE_MIN_AGE_SECONDS_INVALID"):
        Settings(
            environment="development",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root=".data/artifacts",
            jwt_secret="a" * 32,
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            document_purge_min_age_seconds=-1,
        ).validate()


def test_market_data_store_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_MARKET_DATA_STORE_INVALID"):
        Settings(
            environment="development", repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0", artifact_root=".data/artifacts",
            jwt_secret="a" * 32, bootstrap_admin_username="",
            bootstrap_admin_password="", market_data_store="unknown",
        ).validate()
