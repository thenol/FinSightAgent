"""Operational health projection for configured market-data providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.market.provider import MarketDataCapability, MarketDataProvider


@dataclass(frozen=True)
class MarketProviderHealth:
    provider: str
    configured_status: str
    operational_status: str
    supported_markets: tuple[str, ...]
    supported_intervals: tuple[str, ...]
    last_success_at: datetime | None
    checked_at: datetime
    reason: str | None = None


def project_provider_health(provider: MarketDataProvider) -> list[MarketProviderHealth]:
    """Return health for the selected provider and nested fallback sources."""
    sources = []
    primary = getattr(provider, "primary", None)
    fallback = getattr(provider, "fallback", None)
    if primary is not None:
        sources.append(primary)
    if fallback is not None:
        sources.append(fallback)
    if not sources:
        sources.append(provider)
    checked_at = datetime.now(timezone.utc)
    return [_project_one(item, checked_at) for item in sources]


def _project_one(provider: MarketDataProvider, checked_at: datetime) -> MarketProviderHealth:
    capability: MarketDataCapability = provider.capability
    if capability.status in {"unavailable", "unsupported"}:
        operational_status = "unavailable"
    elif capability.last_success_at is None:
        operational_status = "unknown"
    else:
        operational_status = "healthy"
    return MarketProviderHealth(
        provider=capability.provider,
        configured_status=capability.status,
        operational_status=operational_status,
        supported_markets=tuple(capability.supported_markets),
        supported_intervals=tuple(capability.supported_intervals),
        last_success_at=capability.last_success_at,
        checked_at=checked_at,
        reason=capability.reason,
    )
