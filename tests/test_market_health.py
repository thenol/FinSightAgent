from datetime import timezone

from app.market.health import project_provider_health
from app.market.provider import InMemoryMarketDataProvider, UnavailableMarketDataProvider


def test_health_distinguishes_configured_from_operational() -> None:
    configured = InMemoryMarketDataProvider()
    unavailable = UnavailableMarketDataProvider()

    configured_health = project_provider_health(configured)[0]
    unavailable_health = project_provider_health(unavailable)[0]

    assert configured_health.configured_status == "available"
    assert configured_health.operational_status == "unknown"
    assert unavailable_health.operational_status == "unavailable"
    assert configured_health.checked_at.tzinfo == timezone.utc
