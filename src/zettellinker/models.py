from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    fragment: str | None
    embed: bool
    start: int
    end: int


@dataclass
class Note:
    path: Path
    relative_path: str
    identity: str
    basename: str
    text: str
    clean_text: str
    links: list[WikiLink] = field(default_factory=list)


@dataclass(frozen=True)
class LinkResolution:
    source: str
    target_text: str
    status: str
    resolved_identity: str | None = None
    matches: tuple[str, ...] = ()


@dataclass(frozen=True)
class Suggestion:
    source: str
    target: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "score": self.score}


@dataclass(frozen=True)
class Finding:
    kind: str
    source: str
    target: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "details": self.details,
        }


@dataclass(frozen=True)
class IndexStats:
    embedded: int
    reused: int
    removed: int

    def __getitem__(self, item: str) -> int:
        if item == "embedded":
            return self.embedded
        if item == "reused":
            return self.reused
        if item == "removed":
            return self.removed
        raise KeyError(item)

    def as_dict(self) -> dict[str, int]:
        return {"embedded": self.embedded, "reused": self.reused, "removed": self.removed}


@dataclass(frozen=True)
class DoctorCheck:
    ok: bool
    value: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.value is not None:
            payload["value"] = self.value
        return payload


