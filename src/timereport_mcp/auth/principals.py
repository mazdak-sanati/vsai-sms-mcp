"""Principals, scopes, and token-hash verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

ALL_SCOPES: Final[frozenset[str]] = frozenset(
    {
        "workbooks:read",
        "workbooks:write",
        "sms:read",
        "sms:send",
        "sms:admin",
    }
)


@dataclass(frozen=True, slots=True)
class Principal:
    client_id: str
    tenant_id: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def hash_bearer_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ClientRecord:
    client_id: str
    tenant_id: str
    token_sha256: str
    scopes: frozenset[str]
    synthetic_example: bool = False


class PrincipalRegistry:
    """Server-owned mapping from token hash / client_id to a principal."""

    def __init__(self, clients: list[ClientRecord]) -> None:
        by_hash: dict[str, ClientRecord] = {}
        by_client: dict[str, ClientRecord] = {}
        for client in clients:
            if client.token_sha256 in by_hash:
                raise ValueError(f"Duplicate token hash for client {client.client_id}")
            if client.client_id in by_client:
                raise ValueError(f"Duplicate client_id {client.client_id}")
            unknown = client.scopes - ALL_SCOPES
            if unknown:
                raise ValueError(f"Unknown scopes for {client.client_id}: {sorted(unknown)}")
            by_hash[client.token_sha256] = client
            by_client[client.client_id] = client
        self._by_hash = by_hash
        self._by_client = by_client

    def resolve_token(self, token: str) -> Principal | None:
        record = self._by_hash.get(hash_bearer_token(token))
        if record is None:
            return None
        return Principal(
            client_id=record.client_id,
            tenant_id=record.tenant_id,
            scopes=record.scopes,
        )

    def resolve_client_id(self, client_id: str) -> Principal | None:
        record = self._by_client.get(client_id)
        if record is None:
            return None
        return Principal(
            client_id=record.client_id,
            tenant_id=record.tenant_id,
            scopes=record.scopes,
        )

    def has_synthetic_examples(self) -> bool:
        return any(c.synthetic_example for c in self._by_client.values())

    def clients_for_tenant(self, tenant_id: str) -> list[ClientRecord]:
        return [c for c in self._by_client.values() if c.tenant_id == tenant_id]
