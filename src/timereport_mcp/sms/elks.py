"""Optional 46elks SMS adapter. Never used in Phase 1 acceptance tests."""

from __future__ import annotations

from typing import Any

import httpx

from timereport_mcp.sms.gateway import SmsSendRequest, SmsSendResult

ELKS_SMS_URL = "https://api.46elks.com/a1/sms"


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
        try:
            response = self._client.post(
                ELKS_SMS_URL,
                data=form,
                auth=(self._username, self._password),
            )
        except httpx.TimeoutException:
            return SmsSendResult(outcome="uncertain", detail="timeout")
        except httpx.TransportError as exc:
            return SmsSendResult(outcome="uncertain", detail=type(exc).__name__)

        if 400 <= response.status_code < 500:
            return SmsSendResult(
                outcome="rejected",
                detail=f"http_{response.status_code}",
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
        if not message_id:
            return SmsSendResult(outcome="uncertain", detail="missing_id")
        return SmsSendResult(
            outcome="sent",
            provider_message_id=str(message_id),
        )
