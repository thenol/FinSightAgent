import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# 本地开发时自动加载 .env；生产环境由 shell/容器注入变量，可安全忽略。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

DEFAULT_JWT_SECRET = "development-only-secret-change-me-32-bytes"
PRODUCTION_ENVIRONMENTS = {"production", "prod"}


@dataclass(frozen=True)
class Settings:
    environment: str
    repository: str
    database_url: str
    redis_url: str
    artifact_root: str
    jwt_secret: str
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    settings_fernet_key: str = ""

    # Market data source routing.  ``auto`` means EastMoney with an explicit
    # fallback capability; it never fabricates bars when both sources fail.
    market_data_provider: str = "auto"
    market_data_timeout_seconds: float = 15.0
    market_data_bridge_url: str = "http://127.0.0.1:8765"
    market_archive_root: str = ".data/market"
    market_data_store: str = "local"
    clickhouse_url: str = "clickhouse://localhost:8123/default"
    minio_endpoint: str = "http://localhost:9000"

    default_rate_limit_per_minute: int = 10
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    login_failure_window_seconds: int = 300
    metrics_enabled: bool = False
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    login_failure_window_seconds: int = 300
    metrics_enabled: bool = False
    rsshub_base_url: str = "http://127.0.0.1:1200"
    fetch_timeout_seconds: float = 60.0
    ingest_max_items_per_sync: int = 20
    source_auto_disable_failures: int = 5
    robots_enabled: bool = True
    # Minimum seconds a document must remain soft-deleted before purge (default 7d).
    document_purge_min_age_seconds: int = 7 * 24 * 60 * 60
    # Auto-purge worker interval (default 1h); 0 disables the scheduled job.
    document_purge_interval_seconds: int = 3600
    document_purge_batch_size: int = 100
    # 当事件重要度 >= 该阈值时，主水线自动为其创建工作流（进入 pending）。
    workflow_auto_importance_threshold: float = 0.70
    # 自动触发开关；false 时仍创建事件/卡片，但不自动排队研究工作流。
    workflow_auto_trigger_enabled: bool = True
    # 哪些审核任务类型启用自动审核（report / claim_conflict / merge_review / workflow）。
    auto_review_enabled_types: frozenset[str] = frozenset(
        {"report", "claim_conflict", "merge_review", "workflow", "impact_analysis"}
    )
    # 自动审核最低置信度；规则层返回 1.0，LLM 层必须 >= 该值才生效。
    auto_review_min_confidence: float = 0.85
    # 规则无法判断时是否允许调用 LLM 兜底。
    auto_review_llm_fallback: bool = True
    # Runtime review policy defaults to Agent; database policy may override it.
    review_mode: str = "agent"
    auto_review_disabled: bool = False
    # 已发布事实卡片是否自动生成影响分析。
    auto_impact_analysis_enabled: bool = True
    # 自动生成影响分析的最小事件重要度。
    auto_impact_analysis_importance_threshold: float = 0.70
    # 候选类型积累到该计数后，管理台标记可升格（DD-21 §2.4）。
    candidate_type_promotion_threshold: int = 5

    def validate(self) -> "Settings":
        if self.environment not in {"development", "test", "staging", "production", "prod"}:
            raise ValueError("FINSIGHT_ENV_INVALID")
        if self.repository not in {"memory", "postgresql"}:
            raise ValueError("FINSIGHT_REPOSITORY_INVALID")
        if self.repository == "postgresql":
            parsed = urlparse(self.database_url)
            if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or not parsed.hostname:
                raise ValueError("FINSIGHT_DATABASE_URL_INVALID")
        if self.market_data_provider not in {"auto", "bridge", "eastmoney", "akshare", "none"}:
            raise ValueError("FINSIGHT_MARKET_DATA_PROVIDER_INVALID")
        if self.market_data_timeout_seconds <= 0 or self.market_data_timeout_seconds > 300:
            raise ValueError("FINSIGHT_MARKET_DATA_TIMEOUT_INVALID")
        if urlparse(self.market_data_bridge_url).scheme not in {"http", "https"}:
            raise ValueError("FINSIGHT_MARKET_DATA_BRIDGE_URL_INVALID")
        if not self.market_archive_root:
            raise ValueError("FINSIGHT_MARKET_ARCHIVE_ROOT_INVALID")
        if self.market_data_store not in {"local", "clickhouse", "dual"}:
            raise ValueError("FINSIGHT_MARKET_DATA_STORE_INVALID")
        if not urlparse(self.clickhouse_url).scheme.startswith("clickhouse"):
            raise ValueError("FINSIGHT_CLICKHOUSE_URL_INVALID")
        if urlparse(self.minio_endpoint).scheme not in {"http", "https"}:
            raise ValueError("FINSIGHT_MINIO_ENDPOINT_INVALID")
        if not urlparse(self.redis_url).scheme.startswith("redis"):
            raise ValueError("FINSIGHT_REDIS_URL_INVALID")
        artifact_root = Path(self.artifact_root)
        if not artifact_root.is_absolute() and self.environment in PRODUCTION_ENVIRONMENTS:
            raise ValueError("FINSIGHT_ARTIFACT_ROOT_MUST_BE_ABSOLUTE")
        if self.environment in PRODUCTION_ENVIRONMENTS and (
            self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("FINSIGHT_JWT_SECRET_REQUIRED")
        if self.environment in PRODUCTION_ENVIRONMENTS:
            if not self.settings_fernet_key or len(self.settings_fernet_key) < 32:
                raise ValueError("FINSIGHT_SETTINGS_FERNET_KEY_REQUIRED")
            if self.settings_fernet_key == self.jwt_secret:
                raise ValueError("FINSIGHT_SETTINGS_FERNET_KEY_MUST_DIFFER_FROM_JWT")
        if bool(self.bootstrap_admin_username) != bool(self.bootstrap_admin_password):
            raise ValueError("FINSIGHT_BOOTSTRAP_ADMIN_CREDENTIALS_REQUIRED")
        if self.bootstrap_admin_username and len(self.bootstrap_admin_username) > 128:
            raise ValueError("FINSIGHT_BOOTSTRAP_ADMIN_USERNAME_INVALID")
        if self.bootstrap_admin_password and len(self.bootstrap_admin_password) < 8:
            raise ValueError("FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD_TOO_SHORT")
        if self.login_max_failures < 1 or self.login_max_failures > 100:
            raise ValueError("FINSIGHT_LOGIN_MAX_FAILURES_INVALID")
        if self.login_lockout_seconds < 30 or self.login_lockout_seconds > 86400:
            raise ValueError("FINSIGHT_LOGIN_LOCKOUT_SECONDS_INVALID")
        if self.login_failure_window_seconds < 30 or self.login_failure_window_seconds > 86400:
            raise ValueError("FINSIGHT_LOGIN_FAILURE_WINDOW_SECONDS_INVALID")
        max_purge_age = 365 * 24 * 60 * 60
        if (
            self.document_purge_min_age_seconds < 0
            or self.document_purge_min_age_seconds > max_purge_age
        ):
            raise ValueError("FINSIGHT_DOCUMENT_PURGE_MIN_AGE_SECONDS_INVALID")
        if self.document_purge_interval_seconds < 0:
            raise ValueError("FINSIGHT_DOCUMENT_PURGE_INTERVAL_SECONDS_INVALID")
        if self.document_purge_batch_size < 1 or self.document_purge_batch_size > 10000:
            raise ValueError("FINSIGHT_DOCUMENT_PURGE_BATCH_SIZE_INVALID")
        if not 0.0 <= self.workflow_auto_importance_threshold <= 1.0:
            raise ValueError("FINSIGHT_WORKFLOW_AUTO_IMPORTANCE_THRESHOLD_INVALID")
        if self.review_mode not in {"agent", "human"}:
            raise ValueError("FINSIGHT_REVIEW_MODE_INVALID")
        if not 0.0 <= self.auto_impact_analysis_importance_threshold <= 1.0:
            raise ValueError("FINSIGHT_AUTO_IMPACT_ANALYSIS_IMPORTANCE_THRESHOLD_INVALID")
        if (
            self.candidate_type_promotion_threshold < 1
            or self.candidate_type_promotion_threshold > 1000
        ):
            raise ValueError("FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD_INVALID")
        return self

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.getenv("FINSIGHT_ENV", "development"),
            repository=os.getenv("FINSIGHT_REPOSITORY", "memory"),
            database_url=os.getenv(
                "FINSIGHT_DATABASE_URL",
                "postgresql+psycopg://finsight:finsight@localhost:5432/finsight",
            ),
            redis_url=os.getenv("FINSIGHT_REDIS_URL", "redis://localhost:6379/0"),
            artifact_root=os.getenv("FINSIGHT_ARTIFACT_ROOT", ".data/artifacts"),
            jwt_secret=os.getenv(
                "FINSIGHT_JWT_SECRET",
                DEFAULT_JWT_SECRET,
            ),
            bootstrap_admin_username=os.getenv("FINSIGHT_BOOTSTRAP_ADMIN_USERNAME", ""),
            bootstrap_admin_password=os.getenv("FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD", ""),
            settings_fernet_key=os.getenv("FINSIGHT_SETTINGS_FERNET_KEY", ""),
            market_data_provider=os.getenv("MARKET_DATA_PROVIDER", "auto").strip().lower(),
            market_data_timeout_seconds=float(os.getenv("MARKET_DATA_TIMEOUT_SECONDS", "15")),
            market_data_bridge_url=os.getenv("MARKET_DATA_BRIDGE_URL", "http://127.0.0.1:8765"),
            market_archive_root=os.getenv("MARKET_ARCHIVE_ROOT", ".data/market"),
            market_data_store=os.getenv("MARKET_DATA_STORE", "local").strip().lower(),
            clickhouse_url=os.getenv(
                "CLICKHOUSE_URL", "clickhouse://localhost:8123/default"
            ),
            minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
            default_rate_limit_per_minute=int(
                os.getenv("FINSIGHT_DEFAULT_RATE_LIMIT_PER_MINUTE", "10")
            ),
            login_max_failures=int(os.getenv("FINSIGHT_LOGIN_MAX_FAILURES", "5")),
            login_lockout_seconds=int(os.getenv("FINSIGHT_LOGIN_LOCKOUT_SECONDS", "900")),
            login_failure_window_seconds=int(
                os.getenv("FINSIGHT_LOGIN_FAILURE_WINDOW_SECONDS", "300")
            ),
            metrics_enabled=os.getenv("FINSIGHT_METRICS_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            rsshub_base_url=os.getenv("FINSIGHT_RSSHUB_BASE_URL", "http://127.0.0.1:1200"),
            fetch_timeout_seconds=float(os.getenv("FINSIGHT_FETCH_TIMEOUT_SECONDS", "60")),
            ingest_max_items_per_sync=int(os.getenv("FINSIGHT_INGEST_MAX_ITEMS_PER_SYNC", "20")),
            source_auto_disable_failures=int(
                os.getenv("FINSIGHT_SOURCE_AUTO_DISABLE_FAILURES", "5")
            ),
            robots_enabled=os.getenv("FINSIGHT_ROBOTS_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            document_purge_min_age_seconds=int(
                os.getenv("FINSIGHT_DOCUMENT_PURGE_MIN_AGE_SECONDS", str(7 * 24 * 60 * 60))
            ),
            document_purge_interval_seconds=int(
                os.getenv("FINSIGHT_DOCUMENT_PURGE_INTERVAL_SECONDS", "3600")
            ),
            document_purge_batch_size=int(os.getenv("FINSIGHT_DOCUMENT_PURGE_BATCH_SIZE", "100")),
            workflow_auto_importance_threshold=float(
                os.getenv("FINSIGHT_WORKFLOW_AUTO_IMPORTANCE_THRESHOLD", "0.70")
            ),
            workflow_auto_trigger_enabled=os.getenv(
                "FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "true"
            ).lower()
            in {"1", "true", "yes"},
            auto_review_enabled_types=frozenset(
                t.strip()
                for t in os.getenv(
                    "FINSIGHT_AUTO_REVIEW_ENABLED_TYPES",
                    "report,claim_conflict,merge_review,workflow,impact_analysis",
                ).split(",")
                if t.strip()
            ),
            auto_review_min_confidence=float(
                os.getenv("FINSIGHT_AUTO_REVIEW_MIN_CONFIDENCE", "0.85")
            ),
            auto_review_llm_fallback=os.getenv(
                "FINSIGHT_AUTO_REVIEW_LLM_FALLBACK", "true"
            ).lower()
            in {"1", "true", "yes"},
            review_mode=os.getenv("FINSIGHT_REVIEW_MODE", "agent").strip().lower(),
            auto_review_disabled=os.getenv("FINSIGHT_AUTO_REVIEW_DISABLED", "false").lower()
            in {"1", "true", "yes"},
            auto_impact_analysis_enabled=os.getenv(
                "FINSIGHT_AUTO_IMPACT_ANALYSIS_ENABLED", "true"
            ).lower()
            in {"1", "true", "yes"},
            auto_impact_analysis_importance_threshold=float(
                os.getenv("FINSIGHT_AUTO_IMPACT_ANALYSIS_IMPORTANCE_THRESHOLD", "0.70")
            ),
            candidate_type_promotion_threshold=int(
                os.getenv("FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD", "5")
            ),
        ).validate()
