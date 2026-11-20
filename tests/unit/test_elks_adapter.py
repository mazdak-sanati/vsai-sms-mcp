"""M7 acceptance: 46elks adapter with httpx MockTransport only."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from timereport_mcp.config import ConfigError, load_config
from timereport_mcp.sms.elks import ELKS_SMS_URL, ElksSmsGateway
from timereport_mcp.sms.gateway import SmsSendRequest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"


def test_build_request_fields_and_success_parses_id() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": "s70df59406a1b4643b96f3f91e0bfb7b0",
                "status": "created",
            },
        )

    transport = httpx.MockTransport(handler)
    gateway = ElksSmsGateway(
        username="user",
        password="secret-password",
        sender="SynthCo",
        transport=transport,
    )
    result = gateway.send(
        SmsSendRequest(to_e164="+46700000001", body="Reminder for 2026-10")
    )
    assert result.outcome == "sent"
    assert result.provider_message_id == "s70df59406a1b4643b96f3f91e0bfb7b0"
    assert captured["url"] == ELKS_SMS_URL
    assert captured["auth"] is not None
    assert captured["auth"].startswith("Basic ")
    assert "from=SynthCo" in captured["body"] or "from=SynthCo" in captured["body"].replace(
        "+", " "
    )
    # form-urlencoded
    assert "to=%2B46700000001" in captured["body"] or "to=+46700000001" in captured["body"]
    assert "dontlog=message" in captured["body"]
    assert "message=" in captured["body"]
    gateway.close()


def test_http_400_maps_to_rejected() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"status": "failed"})
    )
    gateway = ElksSmsGateway(
        username="user",
        password="secret-password",
        sender="SynthCo",
        transport=transport,
    )
    result = gateway.send(SmsSendRequest(to_e164="+46700000001", body="x"))
    assert result.outcome == "rejected"
    gateway.close()


def test_timeout_maps_to_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("mock timeout")

    transport = httpx.MockTransport(handler)
    gateway = ElksSmsGateway(
        username="user",
        password="secret-password",
        sender="SynthCo",
        transport=transport,
    )
    result = gateway.send(SmsSendRequest(to_e164="+46700000001", body="x"))
    assert result.outcome == "uncertain"
    assert result.detail == "timeout"
    gateway.close()


def test_elks_without_live_send_refuses_config() -> None:
    with pytest.raises(ConfigError, match="SMS_LIVE_SEND=true"):
        load_config(
            EXAMPLE_CONFIG,
            environ={
                "SMS_PROVIDER": "elks",
                "SMS_LIVE_SEND": "false",
                "TIMEREPORT_DATA_DIR": str(ROOT / "data" / "test-m7"),
            },
        )


def test_credentials_not_in_mapped_error_or_logs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad auth secret-password")

    transport = httpx.MockTransport(handler)
    gateway = ElksSmsGateway(
        username="user",
        password="secret-password",
        sender="SynthCo",
        transport=transport,
    )
    result = gateway.send(SmsSendRequest(to_e164="+46700000001", body="x"))
    from dataclasses import asdict

    dumped = json.dumps(asdict(result))
    assert "secret-password" not in dumped
    assert result.detail == "http_400"
    gateway.close()
