"""Optional 46elks SMS adapter. Never used in Phase 1 acceptance tests."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from timereport_mcp.observability.logging import get_logger
from timereport_mcp.sms.gateway import SmsSendRequest, SmsSendResult

ELKS_SMS_URL = "https://api.46elks.com/a1/sms"
logger = get_logger(__name__)


def _redact_e164(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 4:
        return "***"
    return f"+***{digits[-4:]}"


def _sms_trace_enabled() -> bool:
    return os.environ.get("TIMEREPORT_SMS_TRACE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class ElksSmsGateway:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        sender: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._sender = sender
        self._timeout = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def build_form(self, request: SmsSendRequest) -> dict[str, str]:
        return {
            "from": request.sender or self._sender,
            "to": request.to_e164,
            "message": request.body,
            "dontlog": "message",
        }

    def send(self, request: SmsSendRequest) -> SmsSendResult:
        form = self.build_form(request)
        if _sms_trace_enabled():
            logger.info(
                "elks send start from=%s to=%s body_chars=%s",
                form.get("from"),
                _redact_e164(form.get("to", "")),
                len(form.get("message") or ""),
            )
        try:
            response = self._client.post(
                ELKS_SMS_URL,
                data=form,
                auth=(self._username, self._password),
            )
        except httpx.TimeoutException:
            if _sms_trace_enabled():
                logger.warning("elks send timeout")
            return SmsSendResult(outcome="uncertain", detail="timeout")
        except httpx.TransportError as exc:
            if _sms_trace_enabled():
                logger.warning("elks transport error %s", type(exc).__name__)
            return SmsSendResult(outcome="uncertain", detail=type(exc).__name__)

        if _sms_trace_enabled():
            logger.info(
                "elks http_status=%s content_type=%s body_chars=%s",
                response.status_code,
                response.headers.get("content-type"),
                len(response.text or ""),
            )

        if 400 <= response.status_code < 500:
            # Response body may explain invalid sender / number; keep short, no secrets.
            snippet = (response.text or "").replace("\n", " ")[:180]
            if _sms_trace_enabled():
                logger.warning("elks rejected body_snip=%s", snippet)
            return SmsSendResult(
                outcome="rejected",
                detail=f"http_{response.status_code}:{snippet}",
            )
        if response.status_code >= 500:
            return SmsSendResult(
                outcome="uncertain",
                detail=f"http_{response.status_code}",
            )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError:
            return SmsSendResult(outcome="uncertain", detail="invalid_json")

        message_id = payload.get("id")
        provider_status = payload.get("status")
        direction = payload.get("direction")
        if _sms_trace_enabled():
            # Do not log message text or raw destination from payload.
            logger.info(
                "elks accepted id=%s status=%s direction=%s from=%s to=%s",
                message_id,
                provider_status,
                direction,
                payload.get("from"),
                _redact_e164(str(payload.get("to") or "")),
            )
        if not message_id:
            return SmsSendResult(outcome="uncertain", detail="missing_id")

        # When tracing, re-check the message once: 46elks often returns
        # status=created on POST, then fails delivery shortly after.
        detail = f"status={provider_status}" if provider_status is not None else None
        if _sms_trace_enabled() and message_id:
            # created -> sent -> delivered|failed. Early poll can miss failure.
            follow_status = provider_status
            follow: dict[str, Any] | None = None
            for delay in (1.0, 2.0, 3.0):
                time.sleep(delay)
                follow = self._fetch_status(str(message_id))
                if follow is None:
                    continue
                follow_status = follow.get("status")
                logger.info(
                    "elks followup id=%s status=%s cost=%s parts=%s",
                    message_id,
                    follow_status,
                    follow.get("cost"),
                    follow.get("parts"),
                )
                if follow_status in {"delivered", "failed"}:
                    break
            detail = f"status={provider_status};followup={follow_status}"
            if follow_status == "failed":
                return SmsSendResult(
                    outcome="rejected",
                    provider_message_id=str(message_id),
                    detail=detail,
                )

        return SmsSendResult(
            outcome="sent",
            provider_message_id=str(message_id),
            detail=detail,
        )

    def _fetch_status(self, message_id: str) -> dict[str, Any] | None:
        try:
            response = self._client.get(
                f"{ELKS_SMS_URL}/{message_id}",
                auth=(self._username, self._password),
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("elks status fetch failed %s", type(exc).__name__)
            return None
        if response.status_code != 200:
            logger.warning("elks status http_status=%s", response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
