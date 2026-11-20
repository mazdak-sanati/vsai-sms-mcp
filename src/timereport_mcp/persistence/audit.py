"""Append-only audit events with a hash chain."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    material = prev_hash + _canonical(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AuditEvent:
    seq: int
    prev_hash: str
    row_hash: str
    payload: dict[str, Any]


class FileAuditLog:
    """Simple JSONL audit log used until the SQL store is wired in M6."""

    GENESIS = "0" * 64

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, payload: dict[str, Any]) -> AuditEvent:
        with self._lock:
            events = self.read_all()
            prev = events[-1].row_hash if events else self.GENESIS
            seq = (events[-1].seq + 1) if events else 1
            enriched = {
                **payload,
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
            }
            digest = row_hash(prev, enriched)
            event = AuditEvent(seq=seq, prev_hash=prev, row_hash=digest, payload=enriched)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "seq": event.seq,
                            "prev_hash": event.prev_hash,
                            "row_hash": event.row_hash,
                            "payload": event.payload,
                        },
                        default=str,
                    )
                    + "\n"
                )
            return event

    def read_all(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        events: list[AuditEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            events.append(
                AuditEvent(
                    seq=int(raw["seq"]),
                    prev_hash=str(raw["prev_hash"]),
                    row_hash=str(raw["row_hash"]),
                    payload=dict(raw["payload"]),
                )
            )
        return events

    def verify_chain(self) -> bool:
        events = self.read_all()
        prev = self.GENESIS
        for event in events:
            expected = row_hash(prev, event.payload)
            if event.prev_hash != prev or event.row_hash != expected:
                return False
            prev = event.row_hash
        return True
