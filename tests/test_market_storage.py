import json
from datetime import datetime, timezone

from app.market.ingest import MarketIngestBatch, MarketIngestRun
from app.market.provider import MarketBar
from app.market.replay import LocalArchiveMarketDataProvider
from app.market.storage import (
    LocalMarketBatchStore,
    MarketStorageReceipt,
    MirroredMarketBatchStore,
)


def test_local_market_batch_store_is_atomic_and_replayable(tmp_path) -> None:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    run = MarketIngestRun(
        run_id="mkt_test",
        provider="test",
        status="succeeded",
        started_at=now,
        finished_at=now,
        as_of=now,
        interval="1d",
        instrument_count=1,
        bar_count=1,
    )
    bar = MarketBar(
        instrument_id="cn:index:000300",
        market="cn",
        symbol="000300",
        interval="1d",
        observed_at=now,
        open=100,
        high=101,
        low=99,
        close=100,
        source="test",
        available_at=now,
        ingested_at=now,
    )
    receipt = LocalMarketBatchStore(str(tmp_path)).write(MarketIngestBatch(run, (bar,)))
    with open(receipt.location, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert receipt.bar_count == 1
    assert receipt.checksum
    assert payload["run"]["run_id"] == "mkt_test"
    assert payload["bars"][0]["close"] == 100


def test_mirrored_store_preserves_primary_when_analytics_mirror_fails(tmp_path) -> None:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    batch = MarketIngestBatch(
        MarketIngestRun(
            run_id="mkt_mirror",
            provider="test",
            status="succeeded",
            started_at=now,
            finished_at=now,
            as_of=now,
            interval="1d",
            instrument_count=0,
            bar_count=0,
        ),
        (),
    )

    class FailingStore:
        def write(self, _batch):
            raise ConnectionError("clickhouse unavailable")

    receipt = MirroredMarketBatchStore(LocalMarketBatchStore(str(tmp_path)), FailingStore()).write(
        batch
    )

    assert receipt.status == "degraded"
    assert receipt.destinations == (receipt.location,)
    assert receipt.warnings == ("mirror_0_failed:ConnectionError",)


def test_mirrored_store_reports_all_successful_destinations(tmp_path) -> None:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    batch = MarketIngestBatch(
        MarketIngestRun(
            run_id="mkt_dual",
            provider="test",
            status="succeeded",
            started_at=now,
            finished_at=now,
            as_of=now,
            interval="1d",
            instrument_count=0,
            bar_count=0,
        ),
        (),
    )

    class MirrorStore:
        def write(self, value):
            return MarketStorageReceipt(
                run_id=value.run.run_id,
                location="clickhouse://market_bars_canonical",
                checksum="mirror",
                bar_count=0,
                persisted_at=now,
            )

    receipt = MirroredMarketBatchStore(LocalMarketBatchStore(str(tmp_path)), MirrorStore()).write(
        batch
    )

    assert receipt.status == "ok"
    assert receipt.destinations[1] == "clickhouse://market_bars_canonical"


def test_local_archive_provider_replays_revision_visible_at_as_of(tmp_path) -> None:
    observed = datetime(2026, 8, 18, tzinfo=timezone.utc)
    store = LocalMarketBatchStore(str(tmp_path))
    for run_id, ingested_at, close in [
        ("mkt_original", datetime(2026, 8, 19, tzinfo=timezone.utc), 100.0),
        ("mkt_revision", datetime(2026, 8, 21, tzinfo=timezone.utc), 102.0),
    ]:
        run = MarketIngestRun(
            run_id=run_id,
            provider="test",
            status="succeeded",
            started_at=ingested_at,
            finished_at=ingested_at,
            as_of=ingested_at,
            interval="1d",
            instrument_count=1,
            bar_count=1,
        )
        bar = MarketBar(
            instrument_id="cn:index:000300",
            market="cn",
            symbol="000300",
            interval="1d",
            observed_at=observed,
            open=close,
            high=close,
            low=close,
            close=close,
            source="test",
            available_at=ingested_at,
            ingested_at=ingested_at,
        )
        store.write(MarketIngestBatch(run, (bar,)))

    provider = LocalArchiveMarketDataProvider(str(tmp_path))
    early = provider.get_bars(
        instrument_ids=["cn:index:000300"],
        start=observed,
        end=observed,
        interval="1d",
        as_of=datetime(2026, 8, 20, tzinfo=timezone.utc),
        limit=10,
    )
    late = provider.get_bars(
        instrument_ids=["cn:index:000300"],
        start=observed,
        end=observed,
        interval="1d",
        as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
        limit=10,
    )

    assert early.bars[0].close == 100.0
    assert late.bars[0].close == 102.0


def test_local_archive_provider_rejects_tampered_batch(tmp_path) -> None:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    run = MarketIngestRun(
        run_id="mkt_tamper",
        provider="test",
        status="succeeded",
        started_at=now,
        finished_at=now,
        as_of=now,
        interval="1d",
        instrument_count=1,
        bar_count=1,
    )
    bar = MarketBar(
        "cn:index:000300",
        "cn",
        "000300",
        "1d",
        now,
        100,
        100,
        100,
        100,
        source="test",
        available_at=now,
        ingested_at=now,
    )
    receipt = LocalMarketBatchStore(str(tmp_path)).write(MarketIngestBatch(run, (bar,)))
    path = tmp_path / "market-batches" / "2026/08/19" / "mkt_tamper.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bars"][0]["close"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = LocalArchiveMarketDataProvider(str(tmp_path)).get_bars(
        instrument_ids=["cn:index:000300"],
        start=now,
        end=now,
        interval="1d",
        as_of=now,
        limit=10,
    )

    assert result.bars == []
    assert f"{receipt.run_id}.json:archive_integrity_unverified" in result.warnings
