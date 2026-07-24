import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from app.application.pipeline import EventResearchPipeline
from app.domain import IngestRun, QuarantineItem, Source
from app.ingestion.fetchers.factory import get_fetcher
from app.ingestion.guard import FetchGuard
from app.ingestion.rate_limiter import RateLimiter
from app.ingestion.rss import RssFeedClient, RssFetchError
from app.platform.ids import new_id
from app.platform.repository import RepositoryProvider
from app.platform.settings import Settings


class IngestSyncService:
    """采集编排：Fetcher 拉列表 → 详情 → EventResearchPipeline。"""

    def __init__(
        self,
        repository: RepositoryProvider,
        pipeline: EventResearchPipeline,
        *,
        client: Optional[RssFeedClient] = None,
        guard: Optional[FetchGuard] = None,
        rate_limiter: Optional[RateLimiter] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.client = client or RssFeedClient()
        self.guard = guard or FetchGuard(settings=settings)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.settings = settings or Settings.from_environment()

    async def sync(
        self,
        source: Source,
        *,
        trigger: str = "manual",
        request_id: Optional[str] = None,
    ) -> dict[str, int | bool | str]:
        started = time.perf_counter()
        now = datetime.now(timezone.utc)
        run = IngestRun(
            id=new_id("igr"),
            source_id=source.id,
            trigger=trigger,
            started_at=now,
            status="running",
            request_id=request_id,
        )
        with self.repository.transaction() as repository:
            repository.save_ingest_run(run)

        if source.status == "disabled":
            return self._finish_run(
                run,
                self._result(0, 0, 0, False, "disabled"),
                status="skipped",
                message="disabled",
                started=started,
            )
        if source.next_retry_at and source.next_retry_at > now:
            return self._finish_run(
                run,
                self._result(0, 0, 0, False, "backoff"),
                status="skipped",
                message="backoff",
                started=started,
            )

        fetcher = get_fetcher(
            source,
            guard=self.guard,
            rate_limiter=self.rate_limiter,
            rss_client=self.client,
        )
        try:
            items = await fetcher.fetch_list()
            meta = fetcher.consume_fetch_meta() if hasattr(fetcher, "consume_fetch_meta") else {}
        except Exception as exc:
            return self._handle_source_failure(source, started, exc, feed_level=True, run=run)

        if meta.get("not_modified"):
            result = self._result(0, 0, 0, True, "not_modified")
            return self._finish_run(
                run, result, status="success", message="not_modified", started=started
            )

        max_items = int(
            self.source_extra_max_items(source)
            or max(1, self.settings.ingest_max_items_per_sync)
        )
        selected = items[:max_items]
        processed = 0
        quarantined = 0
        skipped = 0
        for item in selected:
            try:
                detailed = await fetcher.fetch_detail(item)
                content = detailed.content or detailed.summary
                if not content.strip():
                    content = (detailed.summary or detailed.title or "").strip()
                if not content:
                    raise RssFetchError("EMPTY_CONTENT")
                self.pipeline.process(
                    idempotency_key=f"rss:{source.id}:{detailed.external_id}",
                    source_id=source.id,
                    source_tier=source.trust_tier,
                    external_id=detailed.external_id,
                    url=detailed.url,
                    title=detailed.title,
                    content=content,
                    published_at=self._published_at(detailed.published_raw),
                )
                processed += 1
            except Exception as detail_exc:
                if self._is_benign_skip(detail_exc):
                    skipped += 1
                    continue
                error: Exception = detail_exc
                if item.summary.strip():
                    try:
                        self.pipeline.process(
                            idempotency_key=f"rss:{source.id}:{item.external_id}",
                            source_id=source.id,
                            source_tier=source.trust_tier,
                            external_id=item.external_id,
                            url=item.url,
                            title=item.title,
                            content=item.summary,
                            published_at=self._published_at(item.published_raw),
                        )
                        processed += 1
                        continue
                    except Exception as summary_exc:
                        if self._is_benign_skip(summary_exc):
                            skipped += 1
                            continue
                        error = summary_exc
                quarantined += 1
                self._quarantine(
                    source,
                    item.external_id,
                    item.url,
                    self._error_code(error),
                    str(error),
                )

        updated = replace(
            source,
            etag=meta.get("etag", source.etag),
            last_modified=meta.get("last_modified", source.last_modified),
            last_success_at=datetime.now(timezone.utc),
            status="active",
            consecutive_failures=0,
            next_retry_at=None,
            last_error_code=None,
        )
        with self.repository.transaction() as repository:
            repository.update_source(updated)
        logging.getLogger("finsight.ingest").info(
            "ingest_sync_completed",
            extra={
                "source_id": source.id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        result = self._result(len(selected), processed, quarantined, False, "active")
        result["skipped"] = skipped
        if len(items) > max_items:
            result["truncated"] = True
            result["feed_entries"] = len(items)
        run_status = "partial" if quarantined > 0 else "success"
        message = f"truncated:{max_items}/{len(items)}" if len(items) > max_items else None
        return self._finish_run(run, result, status=run_status, message=message, started=started)

    @staticmethod
    def _is_benign_skip(exc: Exception) -> bool:
        return str(exc) in {
            "IDEMPOTENCY_CONFLICT",
            "DOCUMENT_CONFLICT",
            "IDEMPOTENCY_REFERENCE_MISSING",
        }

    @staticmethod
    def source_extra_max_items(source: Source) -> Optional[int]:
        raw = (source.extra_config or {}).get("max_items_per_sync")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _finish_run(
        self,
        run: IngestRun,
        result: dict[str, int | bool | str],
        *,
        status: str,
        message: Optional[str] = None,
        started: float,
    ) -> dict[str, int | bool | str]:
        finished = replace(
            run,
            status=status,
            finished_at=datetime.now(timezone.utc),
            fetched=int(result.get("fetched", 0)),
            processed=int(result.get("processed", 0)),
            quarantined=int(result.get("quarantined", 0)),
            message=message or str(result.get("status", status)),
        )
        with self.repository.transaction() as repository:
            repository.update_ingest_run(finished)
        result = {**result, "ingest_run_id": finished.id, "run_status": status}
        logging.getLogger("finsight.ingest").info(
            "ingest_run_finished",
            extra={
                "source_id": run.source_id,
                "ingest_run_id": finished.id,
                "run_status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return result

    def _handle_source_failure(
        self,
        source: Source,
        started: float,
        exc: Exception,
        *,
        feed_level: bool,
        run: IngestRun,
    ) -> dict[str, int | bool | str]:
        error_code = self._error_code(exc)
        url = source.feed_url if feed_level else None
        self._quarantine(source, None, url, error_code, str(exc))
        failures = source.consecutive_failures + 1
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=min(2**failures, 300))
        status = "degraded"
        if failures >= self.settings.source_auto_disable_failures:
            status = "disabled"
        with self.repository.transaction() as repository:
            repository.update_source(
                replace(
                    source,
                    status=status,
                    consecutive_failures=failures,
                    next_retry_at=retry_at,
                    last_error_code=error_code,
                )
            )
        logging.getLogger("finsight.ingest").warning(
            "ingest_sync_failed",
            extra={
                "source_id": source.id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        result = self._result(0, 0, 1, False, status)
        return self._finish_run(
            run, result, status="failed", message=error_code, started=started
        )

    @staticmethod
    def _result(
        fetched: int, processed: int, quarantined: int, not_modified: bool, status: str
    ) -> dict[str, int | bool | str]:
        payload: dict[str, int | bool | str] = {
            "fetched": fetched,
            "processed": processed,
            "quarantined": quarantined,
            "not_modified": not_modified,
        }
        if status not in {"active", "not_modified"}:
            payload["status"] = status
        return payload

    def _error_code(self, error: Exception) -> str:
        code = str(error).split(":", 1)[0].strip()
        if code and code.replace("_", "").isalnum() and code.upper() == code:
            return code[:80]
        return f"INGEST_{type(error).__name__.upper()}"[:80]

    def _published_at(self, value: Optional[str]) -> datetime:
        if value:
            try:
                parsed = parsedate_to_datetime(value)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, IndexError):
                pass
        return datetime.now(timezone.utc)

    def _quarantine(
        self,
        source: Source,
        external_id: Optional[str],
        url: Optional[str],
        error_code: str,
        detail: str,
    ) -> None:
        with self.repository.transaction() as repository:
            repository.save_quarantine_item(
                QuarantineItem(
                    id=new_id("qtn"),
                    source_id=source.id,
                    external_id=external_id,
                    url=url,
                    error_code=error_code,
                    detail=detail[:2000],
                    created_at=datetime.now(timezone.utc),
                )
            )


# 向后兼容旧名称
RssSyncService = IngestSyncService
