from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .config import VaultConfig
from .models import Finding, LinkResolution, Note, WikiLink


WIKILINK_RE = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")
FENCE_RE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


MARKDOWN_EXTENSION = ".md"
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkdn", ".txt"}
LINK_ALIAS_SEPARATOR = "|"
LINK_FRAGMENT_DELIMITERS = ("#", "^")

GHOST_LINK_KIND = "ghost_link"
AMBIGUOUS_LINK_KIND = "ambiguous_link"
ORPHAN_NOTE_KIND = "orphan_note"
MISSING_RECIPROCAL_KIND = "missing_reciprocal"

STATUS_RESOLVED = "resolved"
STATUS_UNRESOLVED = "unresolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_ATTACHMENT = "attachment"


def normalize_identity(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    for ext in sorted(MARKDOWN_EXTENSIONS, key=len, reverse=True):
        if cleaned.lower().endswith(ext):
            cleaned = cleaned[:-len(ext)]
            break
    return cleaned.strip("/")


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter:
        ranges.append(frontmatter.span())
    ranges.extend(match.span() for match in FENCE_RE.finditer(text))
    ranges.extend(match.span() for match in INLINE_CODE_RE.finditer(text))
    return sorted(ranges)


def _inside(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


def parse_wikilinks(text: str) -> list[WikiLink]:
    if not text:
        return []
    protected = _protected_ranges(text)
    links: list[WikiLink] = []
    for match in WIKILINK_RE.finditer(text):
        if _inside(match.start(), protected):
            continue
        inner = match.group(2).strip()
        target_part, separator, alias_part = inner.partition(LINK_ALIAS_SEPARATOR)
        target_part = target_part.strip()
        fragment = None
        fragment_positions = [
            pos for pos in (target_part.find(delim) for delim in LINK_FRAGMENT_DELIMITERS) if pos >= 0
        ]
        if fragment_positions:
            split_at = min(fragment_positions)
            fragment = target_part[split_at + 1 :].strip() or None
            target_part = target_part[:split_at].strip()
        links.append(
            WikiLink(
                raw=match.group(0),
                target=normalize_identity(target_part),
                alias=alias_part.strip() if separator and alias_part.strip() else None,
                fragment=fragment,
                embed=bool(match.group(1)),
                start=match.start(),
                end=match.end(),
            )
        )
    return links


def _remove_excluded_sections(text: str, excluded: list[str]) -> str:
    wanted = {item.strip().lstrip("#").strip().casefold() for item in excluded if item.strip()}
    if not wanted:
        return text
    output: list[str] = []
    skipping_level: int | None = None
    for line in text.splitlines(keepends=True):
        heading = HEADING_RE.match(line.rstrip("\r\n"))
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip().casefold()
            if skipping_level is not None and level <= skipping_level:
                skipping_level = None
            if title in wanted:
                skipping_level = level
                continue
        if skipping_level is None:
            output.append(line)
    return "".join(output)


def clean_for_embedding(text: str, excluded_sections: list[str]) -> str:
    if not text:
        return ""
    cleaned = FRONTMATTER_RE.sub("", text, count=1)
    cleaned = _remove_excluded_sections(cleaned, excluded_sections)
    cleaned = FENCE_RE.sub(" ", cleaned)
    cleaned = INLINE_CODE_RE.sub(" ", cleaned)

    def visible_link(match: re.Match[str]) -> str:
        inner = match.group(2)
        target, separator, alias = inner.partition(LINK_ALIAS_SEPARATOR)
        visible = alias if separator else target
        visible = re.split(r"[#^]", visible, maxsplit=1)[0]
        return visible.replace("_", " ").replace("/", " ")

    cleaned = WIKILINK_RE.sub(visible_link, cleaned)
    cleaned = re.sub(r"(?m)^#{1,6}\s*", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _excluded(relative_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in patterns)


def _included(relative_path: str, patterns: list[str]) -> bool:
    if not patterns or patterns == ["**/*.md"]:
        return True
    for pattern in patterns:
        if fnmatch.fnmatchcase(relative_path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(relative_path, pattern[3:]):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(Path(relative_path).name, pattern[3:]):
            return True
    return False


def _in_target_paths(relative_path: str, targets: list[str]) -> bool:
    if not targets:
        return True
    relative = relative_path.replace("\\", "/").strip("/")
    folded_relative = relative.casefold()
    for raw_target in targets:
        target = raw_target.replace("\\", "/").strip().strip("/")
        folded_target = target.casefold()
        if not target or folded_relative == folded_target or folded_relative.startswith(folded_target + "/"):
            return True
    return False


def discover_notes(vault: Path, config: VaultConfig) -> list[Note]:
    vault = vault.resolve()
    if not vault.exists():
        return []
    notes: list[Note] = []
    allowed_hidden = {name.strip("./") for name in config.include_hidden}

    def _on_walk_error(err: OSError) -> None:
        print(f"Warning: Permission denied reading folder: {err}", file=sys.stderr)

    for root, directories, files in os.walk(vault, followlinks=False, onerror=_on_walk_error):
        root_path = Path(root)
        directories[:] = [
            name
            for name in directories
            if not (root_path / name).is_symlink()
            and (not name.startswith(".") or name in allowed_hidden)
        ]
        for filename in files:
            path = root_path / filename
            if path.is_symlink() or path.suffix.lower() not in MARKDOWN_EXTENSIONS:
                continue

            relative = path.relative_to(vault).as_posix()
            rel_fold = relative.casefold()
            name_fold = filename.casefold()

            if config.exclude_name_contains:
                if any(
                    fragment.strip().casefold() in name_fold
                    or fragment.strip().casefold() in rel_fold
                    for fragment in config.exclude_name_contains
                    if fragment.strip()
                ):
                    continue

            if config.exclude_files:
                if any(
                    rel_fold == ef.replace("\\", "/").strip("/").casefold()
                    or rel_fold.endswith("/" + ef.replace("\\", "/").strip("/").casefold())
                    or name_fold == ef.strip().casefold()
                    or ef.strip().casefold() in name_fold
                    or ef.strip().casefold() in rel_fold
                    for ef in config.exclude_files
                    if ef.strip()
                ):
                    continue
            if any(part.startswith(".") and part not in allowed_hidden for part in Path(relative).parts[:-1]):
                continue
            if not _in_target_paths(relative, config.target_paths):
                continue
            if not _included(relative, config.include_globs) or _excluded(relative, config.exclude_globs):
                continue
            text = path.read_text(encoding="utf-8")
            identity = normalize_identity(relative)
            notes.append(
                Note(
                    path=path,
                    relative_path=relative,
                    identity=identity,
                    basename=Path(identity).name,
                    text=text,
                    clean_text=clean_for_embedding(text, config.exclude_sections),
                    links=parse_wikilinks(text),
                )
            )
    return sorted(notes, key=lambda note: note.identity)


class VaultGraph:
    def __init__(self, notes: list[Note]):
        self.notes = notes
        self.by_identity = {note.identity: note for note in notes}
        self.by_basename: dict[str, list[Note]] = {}
        for note in notes:
            self.by_basename.setdefault(note.basename, []).append(note)
        self.resolutions: list[LinkResolution] = []
        self.outgoing: dict[str, set[str]] = {note.identity: set() for note in notes}
        self.incoming: dict[str, set[str]] = {note.identity: set() for note in notes}
        self._resolve_all()

    def resolve_target(self, source: str, target: str) -> LinkResolution:
        normalized = normalize_identity(target)
        if "/" in normalized:
            note = self.by_identity.get(normalized)
            if note:
                return LinkResolution(source, target, STATUS_RESOLVED, note.identity)
            if Path(normalized).suffix.lower() not in {"", MARKDOWN_EXTENSION}:
                return LinkResolution(source, target, STATUS_ATTACHMENT)
            return self._missing(source, target, normalized)
        matches = self.by_basename.get(normalized, [])
        if len(matches) == 1:
            return LinkResolution(source, target, STATUS_RESOLVED, matches[0].identity)
        if len(matches) > 1:
            return LinkResolution(
                source,
                target,
                STATUS_AMBIGUOUS,
                matches=tuple(note.identity for note in matches),
            )
        if Path(normalized).suffix.lower() not in {"", MARKDOWN_EXTENSION}:
            return LinkResolution(source, target, STATUS_ATTACHMENT)
        return self._missing(source, target, normalized)

    def _missing(self, source: str, target: str, normalized: str) -> LinkResolution:
        case_matches = tuple(
            identity for identity in self.by_identity if identity.casefold() == normalized.casefold()
        )
        if not case_matches and "/" not in normalized:
            case_matches = tuple(
                note.identity for note in self.notes if note.basename.casefold() == normalized.casefold()
            )
        return LinkResolution(source, target, STATUS_UNRESOLVED, matches=case_matches)

    def _resolve_all(self) -> None:
        for note in self.notes:
            for link in note.links:
                resolution = self.resolve_target(note.identity, link.target)
                if resolution.status == STATUS_ATTACHMENT:
                    continue
                self.resolutions.append(resolution)
                if resolution.status == STATUS_RESOLVED and resolution.resolved_identity:
                    self.outgoing[note.identity].add(resolution.resolved_identity)
                    self.incoming[resolution.resolved_identity].add(note.identity)

    def connected(self, left: str, right: str) -> bool:
        return right in self.outgoing[left] or left in self.outgoing[right]

    def shortest_target(self, identity: str) -> str:
        note = self.by_identity[identity]
        return note.basename if len(self.by_basename[note.basename]) == 1 else note.identity

    def findings(self, config: VaultConfig) -> list[Finding]:
        findings: list[Finding] = []
        for resolution in self.resolutions:
            if resolution.status == STATUS_UNRESOLVED and config.audits.ghost_links:
                details = {"case_mismatch_candidates": list(resolution.matches)} if resolution.matches else {}
                findings.append(Finding(GHOST_LINK_KIND, resolution.source, resolution.target_text, details))
            elif resolution.status == STATUS_AMBIGUOUS and config.audits.ambiguous_links:
                findings.append(
                    Finding(AMBIGUOUS_LINK_KIND, resolution.source, resolution.target_text, {"matches": list(resolution.matches)})
                )
        if config.audits.orphan_notes:
            for note in self.notes:
                if not self.outgoing[note.identity] and not self.incoming[note.identity]:
                    findings.append(Finding(ORPHAN_NOTE_KIND, note.identity))
        if config.audits.reciprocal_mode == "audit":
            for source, targets in self.outgoing.items():
                for target in targets:
                    if source not in self.outgoing[target]:
                        findings.append(Finding(MISSING_RECIPROCAL_KIND, source, target))
        return findings

