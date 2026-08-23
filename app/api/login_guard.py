"""In-memory login failure tracking keyed by client IP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock


@dataclass(frozen=True)
class LoginGuardSettings:
    max_failures: int = 5
    lockout_seconds: int = 900
    failure_window_seconds: int = 300


class LoginGuard:
    """Track failed login attempts and temporarily lock abusive client IPs."""

    def __init__(self, settings: LoginGuardSettings) -> None:
        self._settings = settings
        self._lock = Lock()
        self._failures: dict[str, list[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}

    def check_allowed(self, client_key: str) -> tuple[bool, int | None]:
        now = datetime.now(timezone.utc)
        with self._lock:
            locked_until = self._locked_until.get(client_key)
            if locked_until is not None and locked_until > now:
                return False, int((locked_until - now).total_seconds()) + 1
            if locked_until is not None:
                self._locked_until.pop(client_key, None)
                self._failures.pop(client_key, None)
            return True, None

    def record_failure(self, client_key: str) -> None:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=self._settings.failure_window_seconds)
        with self._lock:
            attempts = [ts for ts in self._failures.get(client_key, []) if ts >= window_start]
            attempts.append(now)
            if len(attempts) >= self._settings.max_failures:
                self._locked_until[client_key] = now + timedelta(
                    seconds=self._settings.lockout_seconds
                )
                self._failures.pop(client_key, None)
                return
            self._failures[client_key] = attempts

    def record_success(self, client_key: str) -> None:
        with self._lock:
            self._failures.pop(client_key, None)
            self._locked_until.pop(client_key, None)


def client_ip_from_request(forwarded_for: str | None, host: str | None) -> str:
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    return host or "unknown"
