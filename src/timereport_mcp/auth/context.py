"""Request principal resolution for tools."""

from __future__ import annotations

from contextvars import ContextVar

from timereport_mcp.auth.principals import Principal, PrincipalRegistry
from timereport_mcp.models import ToolResult

_stdio_principal: ContextVar[Principal | None] = ContextVar(
    "timereport_stdio_principal", default=None
)
_test_principal: ContextVar[Principal | None] = ContextVar(
    "timereport_test_principal", default=None
)


def install_stdio_principal(principal: Principal) -> None:
    _stdio_principal.set(principal)


def clear_stdio_principal() -> None:
    _stdio_principal.set(None)


def install_test_principal(principal: Principal | None) -> None:
    """Test-only override. Ignored when an HTTP access token is present."""
    _test_principal.set(principal)


def resolve_principal(registry: PrincipalRegistry) -> Principal | None:
    """Resolve the caller principal.

    Order:
    1. Verified HTTP access token (via SDK get_access_token), if available
    2. stdio-bound principal
    3. test-only override (never overrides an HTTP token)
    """
    access_token = _try_get_access_token()
    if access_token is not None:
        client_id = getattr(access_token, "client_id", None)
        if not client_id:
            return None
        return registry.resolve_client_id(str(client_id))

    stdio = _stdio_principal.get()
    if stdio is not None:
        return stdio

    return _test_principal.get()


def require_principal(
    registry: PrincipalRegistry,
    *,
    scope: str | None = None,
) -> tuple[Principal | None, ToolResult | None]:
    principal = resolve_principal(registry)
    if principal is None:
        return None, ToolResult.failure("forbidden", "Authentication required.")
    if scope is not None and not principal.has_scope(scope):
        return None, ToolResult.failure(
            "forbidden",
            f"Missing required scope: {scope}",
            details={"scope": scope},
        )
    return principal, None


def _try_get_access_token() -> object | None:
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:
        return None
    try:
        return get_access_token()
    except Exception:
        return None
