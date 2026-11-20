"""M1 acceptance: config loading and principal resolution."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from timereport_mcp.auth import Principal, hash_bearer_token
from timereport_mcp.auth.context import (
    clear_stdio_principal,
    install_stdio_principal,
    install_test_principal,
    require_principal,
    resolve_principal,
)
from timereport_mcp.config import ConfigError, load_config, require_stdio_tenant

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
TOKEN_B = "tenant-b-token-synthetic"


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(ROOT / "data" / "test"),
    }
    env.update(overrides)
    return env


def test_example_config_loads_two_synthetic_tenants() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    assert set(config.tenants) == {"tenant-a", "tenant-b"}
    assert config.workbook_for("tenant-a", "tenant-a-timesheet") is not None
    assert config.workbook_for("tenant-a", "tenant-b-timesheet") is None
    assert config.clients.has_synthetic_examples() is True


def test_token_hash_resolves_expected_tenant() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    assert principal.tenant_id == "tenant-a"
    assert principal.client_id == "client-tenant-a"
    assert hash_bearer_token(TOKEN_A) == hashlib.sha256(TOKEN_A.encode()).hexdigest()

    other = config.clients.resolve_token(TOKEN_B)
    assert other is not None
    assert other.tenant_id == "tenant-b"


def test_unknown_token_yields_no_principal() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    assert config.clients.resolve_token("not-a-real-token") is None


def test_stdio_tenant_required_and_must_exist() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    with pytest.raises(ConfigError, match="TIMEREPORT_STDIO_TENANT"):
        require_stdio_tenant(config)

    with pytest.raises(ConfigError, match="not a configured tenant"):
        load_config(
            EXAMPLE_CONFIG,
            environ=_base_env(TIMEREPORT_STDIO_TENANT="unknown-tenant"),
        )

    ok = load_config(
        EXAMPLE_CONFIG,
        environ=_base_env(TIMEREPORT_STDIO_TENANT="tenant-a"),
    )
    assert require_stdio_tenant(ok) == "tenant-a"


def test_path_outside_workbook_roots_fails(tmp_path: Path) -> None:
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"not-used")
    root_a = tmp_path / "tenant-a"
    root_a.mkdir()
    calendars = tmp_path / "calendars"
    calendars.mkdir()
    (calendars / "none.toml").write_text(
        'calendar_id = "none"\ndates = []\n', encoding="utf-8"
    )
    cfg = tmp_path / "bad.toml"
    cfg.write_text(
        f"""
calendars = ["calendars/none.toml"]

[[tenants]]
tenant_id = "t1"
workbook_roots = ["{root_a.as_posix()}"]

[tenants.holiday]
mode = "none_confirmed"
range_start = "2026-01-01"
range_end = "2026-12-31"

[[tenants.workbooks]]
workbook_id = "wb1"
path = "{outside.as_posix()}"

[[clients]]
client_id = "c1"
tenant_id = "t1"
token_sha256 = "{"a" * 64}"
scopes = ["workbooks:read"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="outside configured workbook_roots"):
        load_config(cfg, environ=_base_env())


def test_llm_prefixed_env_rejected() -> None:
    with pytest.raises(ConfigError, match="LLM-prefixed"):
        load_config(
            EXAMPLE_CONFIG,
            environ=_base_env(LLM_API_KEY="should-not-be-loaded"),
        )


def test_elks_without_live_send_refuses_startup() -> None:
    with pytest.raises(ConfigError, match="SMS_LIVE_SEND=true"):
        load_config(
            EXAMPLE_CONFIG,
            environ=_base_env(SMS_PROVIDER="elks", SMS_LIVE_SEND="false"),
        )


def test_scope_check_via_require_principal() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    install_test_principal(principal)
    try:
        got, err = require_principal(config.clients, scope="workbooks:read")
        assert err is None
        assert got is not None
        limited = Principal(
            client_id="limited",
            tenant_id="tenant-a",
            scopes=frozenset({"workbooks:read"}),
        )
        install_test_principal(limited)
        _, forbidden = require_principal(config.clients, scope="sms:send")
        assert forbidden is not None
        assert forbidden.error is not None
        assert forbidden.error.code == "forbidden"
    finally:
        install_test_principal(None)


def test_stdio_principal_used_when_no_http_token() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_base_env())
    principal = config.clients.resolve_client_id("client-tenant-a")
    assert principal is not None
    install_stdio_principal(principal)
    try:
        assert resolve_principal(config.clients) == principal
    finally:
        clear_stdio_principal()
