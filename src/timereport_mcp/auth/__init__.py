"""Client identification, capabilities, and tenancy."""

from timereport_mcp.auth.principals import (
    ALL_SCOPES,
    ClientRecord,
    Principal,
    PrincipalRegistry,
    hash_bearer_token,
)

__all__ = [
    "ALL_SCOPES",
    "ClientRecord",
    "Principal",
    "PrincipalRegistry",
    "hash_bearer_token",
]
