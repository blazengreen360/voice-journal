from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class JournalEntry:
    title: str
    body: str
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    saved_at: str | None = None
    mood: str | None = None
    tags: list[str] = field(default_factory=list)
    voice_name: str = "Lucy"


@dataclass(slots=True)
class SessionTurn:
    entry_id: str
    turn_index: int
    role: str
    text: str
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)


@dataclass(slots=True)
class EntryPhoto:
    entry_id: str
    file_path: str
    sort_order: int = 0
    caption: str = ""
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)


@dataclass(slots=True)
class ActiveSession:
    """In-memory state. Never written to DB until save."""

    entry_id: str
    voice_name: str
    turns: list[dict[str, str]] = field(default_factory=list)

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text})

    def user_turn_count(self) -> int:
        return sum(1 for turn in self.turns if turn["role"] == "user")

    def format_for_prompt(self) -> str:
        return "\n".join(
            f"{'Assistant' if turn['role'] == 'assistant' else 'User'}: {turn['text']}"
            for turn in self.turns
        )

    def snapshot(self) -> "ActiveSession":
        return ActiveSession(
            entry_id=self.entry_id,
            voice_name=self.voice_name,
            turns=[dict(turn) for turn in self.turns],
        )
