"""Small security helpers shared by adapters and the engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

CLIENT_DB_ERROR = "Database execution failed. See server logs for details."

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "dsn",
    "password",
    "secret",
    "token",
)
_RAW_PAYLOAD_KEYS = frozenset({"sql", "effective_sql", "stated_task"})

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-api[0-9A-Za-z_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"pypi-[A-Za-z0-9_-]+"),
    re.compile(r"postgresql://[^@\s]+@"),
)


def client_db_error(exc: BaseException) -> str:
    """Generic database error safe to return to clients."""
    return f"{CLIENT_DB_ERROR} ({type(exc).__name__})"


def diagnostic_error(exc: BaseException) -> dict[str, str]:
    """Structured error detail for internal logs after redaction."""
    return {
        "type": type(exc).__name__,
        "message": redact_text(str(exc)),
    }


def redact_text(value: str) -> str:
    """Best-effort masking for secrets embedded in free-form text."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: _mask_match(m.group(0)), redacted)
    return redacted


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys and obvious secret-looking strings."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if key_s in _RAW_PAYLOAD_KEYS or _sensitive_key(key_s):
                out[key_s] = "[REDACTED]"
            else:
                out[key_s] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def audit_safe(entry: dict[str, Any]) -> dict[str, Any]:
    """Redact high-risk audit payload fields before they hit disk."""
    hashes: dict[str, Any] = {}
    for key in ("sql", "effective_sql", "stated_task"):
        if key in entry and entry[key] is not None:
            raw = str(entry[key])
            hashes[f"{key}_sha256"] = sha256(raw.encode("utf-8")).hexdigest()
            hashes[f"{key}_redacted"] = True
    safe = redact(entry)
    safe.update(hashes)
    return safe


def _sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _mask_match(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql://[REDACTED]@"
    if len(value) <= 12:
        return "[REDACTED]"
    return f"{value[:4]}...[REDACTED]...{value[-4:]}"
