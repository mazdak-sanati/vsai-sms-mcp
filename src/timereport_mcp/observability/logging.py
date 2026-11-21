"""Structured logging to stderr with PII redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

# Require a phone-like token (optional +, then 8+ digits with optional separators).
# Avoid matching hex message ids such as ...fa27151648c4.
_PHONE_RE = re.compile(r"(?<![A-Za-z0-9])\+?\d(?:[\d\s\-()]{6,}\d)(?![A-Za-z0-9])")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
_PASSWORD_KEYS = frozenset(
    {
        "password",
        "elks_password",
        "elks_api_password",
        "authorization",
        "token",
        "message",
        "body",
        "telephone",
        "phone",
    }
)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_text(str(a)) for a in record.args)
        return True


def _redact_text(text: str) -> str:
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _PASSWORD_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_text(value)
    return value


def configure_logging(level: int = logging.INFO) -> None:
    """Configure package logging to stderr only (stdout is the stdio MCP wire)."""
    root = logging.getLogger("timereport_mcp")
    if root.handlers:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
