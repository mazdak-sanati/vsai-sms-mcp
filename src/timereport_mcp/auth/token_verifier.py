"""MCP TokenVerifier backed by the server-side principal registry."""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier

from timereport_mcp.auth.principals import PrincipalRegistry


class RegistryTokenVerifier(TokenVerifier):
    def __init__(self, registry: PrincipalRegistry, resource: str) -> None:
        self._registry = registry
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = self._registry.resolve_token(token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id=principal.client_id,
            scopes=sorted(principal.scopes),
            resource=self._resource,
        )
