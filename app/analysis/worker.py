"""影响分析异步生成 worker。

直接从事务 Outbox 表中消费 ``impact_analysis.requested.v1`` 事件，调用
``ImpactAnalysisService`` 生成并持久化结果；失败时按指数退避重试。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.analysis.service import ImpactAnalysisService
from app.platform.repository import RepositoryProvider
from app.platform.settings import Settings

logger = logging.getLogger("finsight.impact_analysis_worker")

MAX_RETRY_DELAY_SECONDS = 300
MAX_ATTEMPTS = 8


class ImpactAnalysisWorker:
    """从 Outbox 消费影响分析请求并异步生成。"""

    def __init__(
        self,
        repository: RepositoryProvider,
        settings: Optional[Settings] = None,
        service: Optional[ImpactAnalysisService] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or Settings.from_environment()
        self.service = service or ImpactAnalysisService(repository, self.settings)

    def run_once(self, batch_size: int = 10) -> int:
        """处理一批待处理的影响分析请求；返回成功生成数量。"""
        now = datetime.now(timezone.utc)
        list_pending = getattr(
            self.repository,
            "list_pending_outbox_by_event_type",
            self.repository.list_pending_outbox,
        )
        messages = list_pending("impact_analysis.requested.v1", batch_size, now=now)
        processed = 0
        for message in messages:
            event_id = message.payload.get("event_id")
            if not event_id:
                self.repository.mark_outbox_published(message.id, now)
                continue
            try:
                self.service.generate(event_id, actor="system:async")
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(message.id, str(exc), message.attempts + 1, now)
                logger.warning(
                    "impact_analysis_failed",
                    extra={"outbox_id": message.id, "event_id": event_id, "error": str(exc)},
                )
            else:
                self.repository.mark_outbox_published(message.id, now)
                processed += 1
                logger.info(
                    "impact_analysis_generated",
                    extra={"outbox_id": message.id, "event_id": event_id},
                )
        return processed

    def _mark_failed(self, message_id: str, error: str, attempts: int, now: datetime) -> None:
        if attempts >= MAX_ATTEMPTS:
            self._mark_dead_lettered(message_id, error, now)
            return
        delay = min(2 ** min(attempts, 20), MAX_RETRY_DELAY_SECONDS)
        next_attempt_at = now + timedelta(seconds=delay)
        marker = getattr(self.repository, "mark_outbox_failed", None)
        if callable(marker):
            marker(message_id, error, next_attempt_at)
        else:
            # 内存测试适配器没有 mark_outbox_failed；直接 publish 避免无限重试。
            self.repository.mark_outbox_published(message_id, now)

    def _mark_dead_lettered(self, message_id: str, error: str, now: datetime) -> None:
        marker = getattr(self.repository, "mark_outbox_dead_lettered", None)
        if callable(marker):
            marker(message_id, error, now)
        else:
            self.repository.mark_outbox_published(message_id, now)

    async def run(
        self,
        stop_event: asyncio.Event,
        *,
        poll_interval: float = 1.0,
        batch_size: int = 10,
    ) -> None:
        """持续轮询 Outbox，直到 stop_event 被设置。"""
        while not stop_event.is_set():
            processed = await asyncio.to_thread(self.run_once, batch_size)
            delay = 0.1 if processed else poll_interval
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
