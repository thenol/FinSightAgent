import asyncio
from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.platform.messaging import BrokerMessage, InboxConsumer, OutboxPublisher
from app.platform.repository import InMemoryRepository, SqlAlchemyRepository

SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
}


class FakeBroker:
    def __init__(self, *, fail_publish: bool = False) -> None:
        self.fail_publish = fail_publish
        self.published = []
        self.messages = []
        self.acknowledged = []

    async def publish(self, stream, message):
        if self.fail_publish:
            raise ConnectionError("redis unavailable")
        self.published.append((stream, message))
        return "1-0"

    async def ensure_group(self, stream, group):
        return None

    async def read_group(self, stream, group, consumer, *, count, block_ms):
        return list(self.messages[:count])

    async def acknowledge(self, stream, group, broker_id):
        self.acknowledged.append(broker_id)


def payload() -> dict:
    return {
        "source_id": "szse",
        "source_tier": "S",
        "external_id": "message-001",
        "url": "https://example.test/message-001",
        "title": "示例公司（000001.SZ）业绩预告",
        "content": "公司预计净利润同比增长20%。",
        "published_at": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    }


def test_outbox_publisher_marks_success() -> None:
    repository = InMemoryRepository()
    EventResearchPipeline(repository).process(idempotency_key="message-1", **payload())
    broker = FakeBroker()

    result = asyncio.run(OutboxPublisher(repository, broker).run_once())

    assert result.published == 1
    assert result.failed == 0
    assert len(broker.published) == 1
    assert repository.list_pending_outbox(10) == []


def test_outbox_failure_is_scheduled_for_retry() -> None:
    repository = InMemoryRepository()
    EventResearchPipeline(repository).process(idempotency_key="message-2", **payload())

    result = asyncio.run(OutboxPublisher(repository, FakeBroker(fail_publish=True)).run_once())

    assert result.failed == 1
    assert repository.outbox[0]["attempts"] == 1
    assert "redis unavailable" in repository.outbox[0]["last_error"]
    assert repository.outbox[0]["published_at"] is None
    assert repository.list_pending_outbox(10) == []


def test_inbox_consumer_runs_handler_once_for_duplicate_message() -> None:
    repository = InMemoryRepository()
    broker = FakeBroker()
    message = BrokerMessage(
        broker_id="1-0",
        message_id="msg_duplicate",
        event_type="fact_card.created.v1",
        aggregate_id="rpt_1",
        payload={"event_id": "evt_1"},
        trace_id="trc_1",
    )
    broker.messages = [message]
    handled = []

    def handler(transaction, value):
        handled.append(value.message_id)
        return {"handled": True}

    consumer = InboxConsumer(
        repository,
        broker,
        handler,
        stream="finsight:events",
        group="test-group",
        consumer="test-consumer",
    )
    first = asyncio.run(consumer.run_once(block_ms=0))
    second = asyncio.run(consumer.run_once(block_ms=0))

    assert first == 1
    assert second == 0
    assert handled == ["msg_duplicate"]
    assert broker.acknowledged == ["1-0", "1-0"]


def test_outbox_moves_to_dead_letter_after_max_attempts() -> None:
    repository = InMemoryRepository()
    EventResearchPipeline(repository).process(idempotency_key="message-dead", **payload())
    repository.outbox[0]["attempts"] = 1

    result = asyncio.run(
        OutboxPublisher(
            repository,
            FakeBroker(fail_publish=True),
            max_attempts=2,
        ).run_once()
    )

    assert result.failed == 1
    assert repository.outbox[0]["dead_lettered_at"] is not None
    assert repository.list_pending_outbox(10) == []


def test_sqlalchemy_inbox_survives_repository_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'inbox.db'}"
    first = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    first.create_schema_for_tests()
    broker = FakeBroker()
    broker.messages = [
        BrokerMessage(
            broker_id="2-0",
            message_id="msg_persistent",
            event_type="fact_card.created.v1",
            aggregate_id="rpt_2",
            payload={},
            trace_id="trc_2",
        )
    ]
    handled = []

    def handler(transaction, value):
        handled.append(value.message_id)
        return {"handled": True}

    first_consumer = InboxConsumer(
        first,
        broker,
        handler,
        stream="finsight:events",
        group="persistent-group",
        consumer="consumer-a",
    )
    assert asyncio.run(first_consumer.run_once(block_ms=0)) == 1

    restarted = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    second_consumer = InboxConsumer(
        restarted,
        broker,
        handler,
        stream="finsight:events",
        group="persistent-group",
        consumer="consumer-b",
    )
    assert asyncio.run(second_consumer.run_once(block_ms=0)) == 0
    assert handled == ["msg_persistent"]
