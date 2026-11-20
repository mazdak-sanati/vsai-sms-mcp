"""Shared Pydantic schemas for tool inputs and results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None

    @classmethod
    def success(cls, data: dict[str, Any]) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            ok=False,
            error=ErrorBody(code=code, message=message, details=details or {}),
        )
