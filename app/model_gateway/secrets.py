"""Encrypt LLM API keys at rest with Fernet."""

from __future__ import annotations

import base64
import hashlib
import os
import re

from cryptography.fernet import Fernet, InvalidToken

from app.platform.settings import PRODUCTION_ENVIRONMENTS, Settings

# Distinct salt so a leaked JWT alone is not enough to decrypt settings ciphertext
# when a dedicated Fernet key is configured. Dev fallback still derives from JWT
# but uses this domain separator.
_DERIVE_DOMAIN = "finsight-llm-settings-v1"

_SK_LIKE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,})\b")


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SecretBox:
        settings = settings or Settings.from_environment()
        raw = (
            settings.settings_fernet_key or os.getenv("FINSIGHT_SETTINGS_FERNET_KEY", "")
        ).strip()
        if raw:
            if len(raw) == 44:
                try:
                    return cls(raw.encode("ascii"))
                except Exception:
                    return cls(cls._derive(raw))
            return cls(cls._derive(raw))
        if settings.environment in PRODUCTION_ENVIRONMENTS:
            raise ValueError("FINSIGHT_SETTINGS_FERNET_KEY_REQUIRED")
        return cls(cls._derive(f"{_DERIVE_DOMAIN}:{settings.jwt_secret}"))

    @staticmethod
    def _derive(secret: str) -> bytes:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("LLM_API_KEY_DECRYPT_FAILED") from exc


def api_key_status_label(configured: bool) -> str:
    """Public status only — never expose key prefixes/suffixes."""
    return "configured" if configured else ""


def redact_sensitive_mapping(value: object) -> object:
    """Recursively redact secret-bearing fields for hashes, logs, and audits."""
    if isinstance(value, dict):
        redacted: dict = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                part in lowered
                for part in ("api_key", "apikey", "authorization", "password", "secret", "token")
            ):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_sensitive_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_mapping(item) for item in value]
    if isinstance(value, str):
        return _SK_LIKE.sub("<redacted-key>", value)
    return value
