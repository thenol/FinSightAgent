import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.platform.repository import OutboxMessage, Repository, RepositoryProvider


@dataclass(frozen=True)
class BrokerMessage:
    broker_id: str
    message_id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    trace_id: str


class MessageBroker(Protocol):
    async def publish(self, stream: str, message: OutboxMessage) -> str: ...

    async def ensure_group(self, stream: str, group: str) -> None: ...

    async def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[BrokerMessage]: ...

    async def acknowledge(self, stream: str, group: str, broker_id: str) -> None: ...


class RedisStreamBroker:
    def __init__(self, redis_url: str) -> None:
        self.redis = Redis.from_url(redis_url, decode_responses=True)

    async def publish(self, stream: str, message: OutboxMessage) -> str:
        return await self.redis.xadd(
            stream,
            {
                "message_id": message.id,
                "event_type": message.event_type,
                "aggregate_id": message.aggregate_id,
                "payload": json.dumps(message.payload, ensure_ascii=False, separators=(",", ":")),
                "trace_id": message.trace_id,
            },
        )

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[BrokerMessage]:
        response = await self.redis.xreadgroup(
            group,
            consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        messages: list[BrokerMessage] = []
        for _, entries in response:
            for broker_id, fields in entries:
                messages.append(
                    BrokerMessage(
                        broker_id=broker_id,
                        message_id=fields["message_id"],
                        event_type=fields["event_type"],
                        aggregate_id=fields["aggregate_id"],
                        payload=json.loads(fields["payload"]),
                        trace_id=fields["trace_id"],
                    )
                )
        return messages

    async def acknowledge(self, stream: str, group: str, broker_id: str) -> None:
        await self.redis.xack(stream, group, broker_id)

    async def close(self) -> None:
        await self.redis.aclose()


@dataclass(frozen=True)
class PublishResult:
    published: int
    failed: int


class OutboxPublisher:
    def __init__(
        self,
        repository: RepositoryProvider,
        broker: MessageBroker,
        *,
        stream: str = "finsight:events",
        max_retry_delay_seconds: int = 300,
        max_attempts: int = 8,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.stream = stream
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self.max_attempts = max_attempts

    async def run_once(self, batch_size: int = 100) -> PublishResult:
        published = 0
        failed = 0
        for message in self.repository.list_pending_outbox(batch_size):
            try:
                await self.broker.publish(self.stream, message)
            except Exception as exc:
                failed += 1
                error = f"{type(exc).__name__}: {exc}"
                if message.attempts + 1 >= self.max_attempts:
                    self.repository.mark_outbox_dead_lettered(
                        message.id, error, datetime.now(timezone.utc)
                    )
                else:
                    delay = min(2 ** min(message.attempts + 1, 20), self.max_retry_delay_seconds)
                    self.repository.mark_outbox_failed(
                        message.id,
                        error,
                        datetime.now(timezone.utc) + timedelta(seconds=delay),
                    )
                logging.getLogger("finsight.outbox").warning(
                    "outbox_publish_failed", extra={"outbox_id": message.id}
                )
            else:
                published += 1
                self.repository.mark_outbox_published(message.id, datetime.now(timezone.utc))
                logging.getLogger("finsight.outbox").info(
                    "outbox_published", extra={"outbox_id": message.id}
                )
        return PublishResult(published=published, failed=failed)


MessageHandler = Callable[[Repository, BrokerMessage], Optional[dict[str, Any]]]


class InboxConsumer:
    def __init__(
        self,
        repository: RepositoryProvider,
        broker: MessageBroker,
        handler: MessageHandler,
        *,
        stream: str,
        group: str,
        consumer: str,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.handler = handler
        self.stream = stream
        self.group = group
        self.consumer = consumer

    async def run_once(self, batch_size: int = 20, block_ms: int = 1000) -> int:
        await self.broker.ensure_group(self.stream, self.group)
        messages = await self.broker.read_group(
            self.stream,
            self.group,
            self.consumer,
            count=batch_size,
            block_ms=block_ms,
        )
        processed = 0
        for message in messages:
            with self.repository.transaction() as repository:
                if repository.is_inbox_processed(self.group, message.message_id):
                    result = None
                else:
                    result = self.handler(repository, message)
                    repository.save_inbox_processed(self.group, message.message_id, result)
                    processed += 1
            await self.broker.acknowledge(self.stream, self.group, message.broker_id)
        return processed
