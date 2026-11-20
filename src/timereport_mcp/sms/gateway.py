"""SMS gateway protocol and mock provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

SmsOutcome = Literal["sent", "rejected", "uncertain"]


@dataclass(slots=True)
class SmsSendRequest:
    to_e164: str
    body: str
    sender: str | None = None


@dataclass(slots=True)
class SmsSendResult:
    outcome: SmsOutcome
    provider_message_id: str | None = None
    detail: str | None = None


class SmsGateway(Protocol):
    def send(self, request: SmsSendRequest) -> SmsSendResult: ...


@dataclass
class MockSmsGateway:
    """In-process fake. Failure mode is constructor-only (tests), not tool args."""

    failure_mode: Literal["none", "timeout", "http_400"] = "none"
    sent: list[SmsSendRequest] = field(default_factory=list)
    _counter: int = 0

    def send(self, request: SmsSendRequest) -> SmsSendResult:
        if self.failure_mode == "timeout":
            return SmsSendResult(outcome="uncertain", detail="mock_timeout")
        if self.failure_mode == "http_400":
            return SmsSendResult(outcome="rejected", detail="mock_http_400")
        self._counter += 1
        self.sent.append(request)
        return SmsSendResult(
            outcome="sent",
            provider_message_id=f"mock-{self._counter}",
        )
