from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .config import VaultConfig
from .models import Suggestion
from .semantic import Embedder, SemanticEngine, cache_directory, normalize
from .vault import (
    FRONTMATTER_RE,
    HEADING_RE,
    INLINE_CODE_RE,
    normalize_identity,
    parse_wikilinks,
)


MIN_PHRASE_WORDS = 3
MAX_CANDIDATE_PHRASES = 160
PHRASE_WINDOWS = (8, 5, 3)
FORBIDDEN_PHRASE_CHARS = "[]`|"
FORBIDDEN_TARGET_CHARS = "[]|"
TRANSACTION_VERSION = 1

SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_-]*")
FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~)")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^\)\n]*\)")


@dataclass(frozen=True)
class Anchor:
    start: int
    end: int
    text: str
    score: float


@dataclass(frozen=True)
class WriteResult:
    transaction: str | None
    modified: tuple[str, ...]
    links_added: int


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate_phrases(text: str, maximum: int = MAX_CANDIDATE_PHRASES) -> list[tuple[int, int, str]]:
    protected = _write_protected_ranges(text, include_headings=True)

    def overlaps(start: int, end: int) -> bool:
        return any(
            start < protected_end and end > protected_start
            for protected_start, protected_end in protected
        )

    candidates: list[tuple[int, int, str]] = []
    for sentence in SENTENCE_RE.finditer(text):
        words = list(WORD_RE.finditer(sentence.group(0)))
        if len(words) < MIN_PHRASE_WORDS:
            continue
        for window in PHRASE_WINDOWS:
            if len(words) < window:
                continue
            stride = max(1, window // 2)
            for start_index in range(0, len(words) - window + 1, stride):
                first = words[start_index]
                last = words[start_index + window - 1]
                start = sentence.start() + first.start()
                end = sentence.start() + last.end()
                phrase = text[start:end]
                if any(character in phrase for character in FORBIDDEN_PHRASE_CHARS):
                    continue
                if not overlaps(start, end):
                    candidates.append((start, end, phrase))
                    if len(candidates) >= maximum:
                        return candidates
    return candidates


def _write_protected_ranges(
    text: str,
    *,
    include_headings: bool,
) -> list[tuple[int, int]]:
    protected: list[tuple[int, int]] = [
        (link.start, link.end) for link in parse_wikilinks(text)
    ]
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter:
        protected.append(frontmatter.span())
    protected.extend(match.span() for match in INLINE_CODE_RE.finditer(text))
    protected.extend(match.span() for match in MARKDOWN_LINK_RE.finditer(text))
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        if FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            protected.append((offset, offset + len(line)))
        elif in_fence or (
            include_headings and HEADING_RE.match(line.rstrip("\r\n"))
        ):
            protected.append((offset, offset + len(line)))
        offset += len(line)

    protected.sort()
    merged: list[tuple[int, int]] = []
    for start, end in protected:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def best_anchor(
    text: str,
    target_vector: np.ndarray,
    embedder: Embedder,
    minimum: float,
) -> Anchor | None:
    candidates = _candidate_phrases(text)
    if not candidates:
        return None
    vectors = embedder.encode([phrase for _, _, phrase in candidates])
    if len(vectors) == 0:
        return None
    scores = np.asarray(vectors, dtype=np.float32) @ normalize(target_vector)
    position = int(np.argmax(scores))
    score = float(scores[position])
    if score < minimum:
        return None
    start, end, phrase = candidates[position]
    return Anchor(start, end, phrase, score)


def _inline(text: str, anchor: Anchor, target: str) -> str | None:
    if any(character in anchor.text for character in FORBIDDEN_PHRASE_CHARS):
        return None
    if any(character in target for character in FORBIDDEN_TARGET_CHARS):
        return None
    replacement = f"[[{target}|{anchor.text}]]"
    updated = text[: anchor.start] + replacement + text[anchor.end :]
    inserted = next(
        (link for link in parse_wikilinks(updated) if link.start == anchor.start),
        None,
    )
    if (
        inserted is None
        or inserted.raw != replacement
        or inserted.target != normalize_identity(target)
        or inserted.alias != anchor.text
    ):
        return None
    return updated


def _connections(text: str, heading: str, target: str) -> str:
    entry = f"[[{target}]]"
    if any(link.target == target for link in parse_wikilinks(text)):
        return text
    lines = text.splitlines(keepends=True)
    protected = _write_protected_ranges(text, include_headings=False)
    offset = 0
    heading_index = None
    for index, line in enumerate(lines):
        line_end = offset + len(line)
        overlaps = any(offset < end and line_end > start for start, end in protected)
        if not overlaps and line.rstrip("\r\n") == heading:
            heading_index = index
            break
        offset = line_end
    if heading_index is None:
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{separator}{heading}\n\n{entry}\n"
    level = len(heading) - len(heading.lstrip("#"))
    insertion = len(lines)
    for index in range(heading_index + 1, len(lines)):
        match = HEADING_RE.match(lines[index].rstrip("\r\n"))
        if match and len(match.group(1)) <= level:
            insertion = index
            break
    prefix = "" if insertion == heading_index + 1 else ("" if lines[insertion - 1].endswith("\n") else "\n")
    lines.insert(insertion, f"{prefix}{entry}\n")
    return "".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_audit(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class LinkWriter:
    def __init__(self, engine: SemanticEngine):
        if engine.graph is None:
            raise RuntimeError("SemanticEngine.refresh must run before LinkWriter")
        self.engine = engine
        self.graph = engine.graph
        self.config: VaultConfig = engine.config
        self.texts = {note.identity: note.text for note in self.graph.notes}
        self.originals = dict(self.texts)
        self.added = 0

    def _has_target(self, source: str, target: str) -> bool:
        for link in parse_wikilinks(self.texts[source]):
            resolution = self.graph.resolve_target(source, link.target)
            if resolution.resolved_identity == target:
                return True
        return False

    def _write_direction(self, source: str, target: str, allow_fallback: bool = True) -> bool:
        if self._has_target(source, target):
            return False
        target_text = self.graph.shortest_target(target)
        vector = self.engine.vector_for(target)
        anchor = None
        if vector is not None:
            anchor = best_anchor(
                self.texts[source],
                vector,
                self.engine.embedder,
                self.config.auto_write.anchor_threshold,
            )
        updated = None
        if anchor:
            updated = _inline(self.texts[source], anchor, target_text)
        if updated is None and allow_fallback:
            updated = _connections(
                self.texts[source], self.config.auto_write.connections_heading, target_text
            )
        if updated is None or updated == self.texts[source]:
            return False
        self.texts[source] = updated
        self.added += 1
        return True

    def apply(self, suggestions: Iterable[Suggestion], source_only: str | None = None) -> WriteResult:
        pairs: dict[tuple[str, str], Suggestion] = {}
        for suggestion in suggestions:
            pair = tuple(sorted((suggestion.source, suggestion.target)))
            current = pairs.get(pair)
            if current is None or suggestion.score > current.score:
                pairs[pair] = suggestion

        for suggestion in sorted(pairs.values(), key=lambda item: (-item.score, item.source, item.target)):
            left, right = suggestion.source, suggestion.target
            if source_only:
                if source_only not in {left, right}:
                    continue
                target = right if left == source_only else left
                self._write_direction(source_only, target)
                continue
            if self.config.auto_write.reciprocal:
                self._write_direction(left, right)
                self._write_direction(right, left)
                continue
            self._apply_asymmetric_suggestion(left, right)

        changed = [identity for identity, text in self.texts.items() if text != self.originals[identity]]
        if not changed:
            return WriteResult(None, (), 0)
        return self._commit_transaction(changed)

    def _apply_asymmetric_suggestion(self, left: str, right: str) -> None:
        left_vector = self.engine.vector_for(right)
        right_vector = self.engine.vector_for(left)
        left_anchor = best_anchor(
            self.texts[left], left_vector, self.engine.embedder, self.config.auto_write.anchor_threshold
        ) if left_vector is not None else None
        right_anchor = best_anchor(
            self.texts[right], right_vector, self.engine.embedder, self.config.auto_write.anchor_threshold
        ) if right_vector is not None else None

        if left_anchor or right_anchor:
            if left_anchor and (not right_anchor or left_anchor.score >= right_anchor.score):
                source, target, anchor = left, right, left_anchor
            else:
                assert right_anchor is not None
                source, target, anchor = right, left, right_anchor
            updated = _inline(
                self.texts[source], anchor, self.graph.shortest_target(target)
            )
            if updated is not None:
                self.texts[source] = updated
                self.added += 1
                return

        left_degree = len(self.graph.outgoing[left])
        right_degree = len(self.graph.outgoing[right])
        source, target = (
            (left, right)
            if (left_degree, left) <= (right_degree, right)
            else (right, left)
        )
        self._write_direction(source, target)

    def _commit_transaction(self, changed: list[str]) -> WriteResult:
        records = []
        for identity in changed:
            note = self.graph.by_identity[identity]
            before, after = self.originals[identity], self.texts[identity]
            records.append(
                {
                    "identity": identity,
                    "relative_path": note.relative_path,
                    "before": before,
                    "before_hash": _digest(before),
                    "after_hash": _digest(after),
                    "after": after,
                }
            )
        transaction = f"{time.time_ns()}-{uuid.uuid4().hex}.json.gz"
        audit_directory = cache_directory(self.engine.vault) / "audit"
        audit_directory.mkdir(parents=True, exist_ok=True)
        audit_path = audit_directory / transaction
        payload = {"version": TRANSACTION_VERSION, "state": "prepared", "created_at": time.time(), "files": records}
        _atomic_audit(audit_path, payload)
        written: list[dict] = []
        try:
            for record in records:
                note = self.graph.by_identity[record["identity"]]
                _atomic_write(note.path, record["after"])
                written.append(record)
        except Exception:
            for record in reversed(written):
                note = self.graph.by_identity[record["identity"]]
                _atomic_write(note.path, record["before"])
            audit_path.rename(audit_path.with_suffix(audit_path.suffix + ".aborted"))
            raise
        for record in records:
            record.pop("after", None)
        payload["state"] = "committed"
        _atomic_audit(audit_path, payload)
        return WriteResult(transaction, tuple(changed), self.added)


def undo_last(vault: Path) -> WriteResult:
    audit_directory = cache_directory(vault.resolve()) / "audit"
    candidates = sorted(audit_directory.glob("*.json.gz"), reverse=True) if audit_directory.exists() else []
    if not candidates:
        return WriteResult(None, (), 0)
    audit_path = candidates[0]
    with gzip.open(audit_path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    conflicts: list[str] = []
    for record in payload.get("files", []):
        path = vault.resolve() / record["relative_path"]
        if not path.exists() or _digest(path.read_text(encoding="utf-8")) != record["after_hash"]:
            conflicts.append(record["relative_path"])
    if conflicts:
        raise RuntimeError(
            "Undo stopped because these files changed after ZettelLinker wrote them: "
            + ", ".join(conflicts)
        )
    restored: list[str] = []
    for record in payload.get("files", []):
        path = vault.resolve() / record["relative_path"]
        _atomic_write(path, record["before"])
        restored.append(record["identity"])
    audit_path.rename(audit_path.with_suffix(audit_path.suffix + ".undone"))
    return WriteResult(audit_path.name, tuple(restored), len(restored))

