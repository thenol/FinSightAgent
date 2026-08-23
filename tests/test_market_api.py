from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.domain import MarketCalibrationVersion
from app.main import create_app
from app.market.provider import InMemoryMarketDataProvider, MarketBar


def _login(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def test_market_capabilities_exposes_provider_and_intervals() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"] == "eastmoney+akshare"
    assert data["supported_intervals"] == ["1d", "5m"]


def test_market_provider_health_does_not_confuse_configuration_with_success() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/providers/health",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert {item["provider"] for item in data} == {"eastmoney", "akshare"}
    assert all(item["operational_status"] in {"unknown", "unavailable", "healthy"} for item in data)


def test_market_instruments_returns_versioned_reference_catalog() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/instruments",
            params={"market": "cn", "instrument_type": "index"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert {item["id"] for item in data} >= {"cn:index:000001", "cn:index:000300"}
    assert all(item["market"] == "cn" and item["instrument_type"] == "index" for item in data)


def test_market_factor_endpoint_exposes_unmapped_status_instead_of_neutral_score() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/factors",
            params={
                "instrument_ids": "cn:index:000300",
                "horizon": 3,
                "as_of": "2026-08-18T00:00:00Z",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    factor = response.json()["data"][0]
    assert factor["status"] == "unavailable"
    assert factor["score"] is None
    assert factor["reason"] == "impact_target_not_mapped"
    assert response.json()["meta"]["rule_version"] == "forecast-factor-v1"


def test_market_calendar_exposes_exchange_schedule() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/calendar",
            params={"market": "hk", "start": "2026-08-17", "end": "2026-08-18"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["warnings"] == []
    assert data["calendar"][0]["timezone"] == "Asia/Hong_Kong"
    assert data["calendar"][0]["source"] == "exchange_calendars:XHKG"


def test_market_bars_rejects_invalid_range_before_provider_call() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/bars",
            params={
                "instrument_ids": "cn:000001",
                "start": "2026-08-31T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MARKET_DATA_RANGE_INVALID"


def test_market_outlooks_rejects_unsupported_horizon_before_provider_call() -> None:
    with TestClient(create_app()) as client:
        token = _login(client)
        response = client.get(
            "/api/v1/market/outlooks",
            params={
                "instrument_ids": "cn:000300",
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-18T00:00:00Z",
                "horizon": 10,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MARKET_OUTLOOK_HORIZON_UNSUPPORTED"


def test_historical_forecast_replay_uses_verified_archive_only(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MARKET_ARCHIVE_ROOT", str(tmp_path))

    class LiveProviderMustNotBeCalled:
        @property
        def capability(self):
            raise AssertionError("historical replay must not inspect the live provider")

        def get_bars(self, **_):
            raise AssertionError("historical replay must not query the live provider")

    with TestClient(create_app()) as client:
        client.app.state.market_data_provider = LiveProviderMustNotBeCalled()
        token = _login(client)
        response = client.post(
            "/api/v1/market/forecast-replays",
            json={
                "instrument_ids": ["cn:index:000300"],
                "forecast_from": "2026-08-16",
                "forecast_to": "2026-08-16",
                "horizon": 1,
                "lookback_days": 120,
                "publication_lag_minutes": 30,
                "max_slots": 10,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["source_provider"] == "local-market-archive"
    assert response.json()["data"]["status"] == "empty"


def test_forecast_run_settlement_and_evaluation_api() -> None:
    as_of = datetime(2026, 8, 18, tzinfo=timezone.utc)
    bars = []
    for offset in range(-79, 2):
        observed_at = as_of + timedelta(days=offset)
        close = 100 + offset * 0.1
        bars.append(
            MarketBar(
                "cn:index:000300",
                "cn",
                "000300",
                "1d",
                observed_at,
                close,
                close,
                close,
                close,
                source="test",
                available_at=observed_at,
            )
        )
    with TestClient(create_app()) as client:
        client.app.state.market_data_provider = InMemoryMarketDataProvider(bars)
        token = _login(client)
        headers = {"Authorization": f"Bearer {token}"}
        issue = client.post(
            "/api/v1/market/forecast-runs",
            json={
                "instrument_ids": ["cn:index:000300"],
                "start": (as_of - timedelta(days=79)).isoformat(),
                "end": as_of.isoformat(),
                "as_of": as_of.isoformat(),
                "horizon": 1,
                "interval": "1d",
                "limit": 500,
            },
            headers=headers,
        )
        assert issue.status_code == 200
        forecast_id = issue.json()["data"][0]["id"]
        assert issue.json()["meta"]["created_count"] == 1

        settle = client.post(
            "/api/v1/market/forecast-outcomes/settle",
            json={
                "forecast_ids": [forecast_id],
                "evaluation_as_of": (as_of + timedelta(days=1)).isoformat(),
            },
            headers=headers,
        )
        assert settle.status_code == 200
        assert settle.json()["data"]["settled_count"] == 1

        evaluation = client.get(
            "/api/v1/market/evaluations",
            params={"instrument_id": "cn:index:000300", "horizon": 1},
            headers=headers,
        )
        assert evaluation.status_code == 200
        report = evaluation.json()["data"]["report"]
        assert report["sample_count"] == 1
        assert report["eligible_count"] == 1
        assert report["coverage"] == 1


def test_calibration_publish_enforces_quality_gate_and_records_published_version() -> None:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    with TestClient(create_app()) as client:
        token = _login(client)
        headers = {"Authorization": f"Bearer {token}"}
        bad = MarketCalibrationVersion(
            "mcv_bad", "market-outlook", "bad", 1, "cn", "draft",
            "temperature_scaling", {"temperature": 1.2},
            {"coverage": 0.5, "brier_score": 0.9, "expected_calibration_error": 0.2},
            now - timedelta(days=365), now - timedelta(days=1), 40, "usr_test", now,
        )
        good = MarketCalibrationVersion(
            "mcv_good", "market-outlook", "2026.08.1", 1, "cn", "draft",
            "temperature_scaling", {"temperature": 1.1},
            {"coverage": 0.9, "brier_score": 0.4, "expected_calibration_error": 0.05},
            now - timedelta(days=365), now - timedelta(days=1), 250, "usr_test", now,
        )
        client.app.state.repository.save_market_calibration_version(bad)
        client.app.state.repository.save_market_calibration_version(good)

        rejected = client.post(
            "/api/v1/market/calibrations/mcv_bad/transition",
            json={"status": "published", "reason": "quality review"},
            headers=headers,
        )
        published = client.post(
            "/api/v1/market/calibrations/mcv_good/transition",
            json={"status": "published", "reason": "passed validation"},
            headers=headers,
        )

        assert rejected.status_code == 409
        assert "MARKET_CALIBRATION_QUALITY_GATE_FAILED" in rejected.json()["error"]["code"]
        assert published.status_code == 200
        assert published.json()["data"]["status"] == "published"
