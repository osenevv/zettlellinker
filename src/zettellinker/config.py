from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


APP_NAME = "zettellinker"
CONFIG_NAME = ".zettellinker.yml"

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SIMILARITY_THRESHOLD = 0.60
DEFAULT_LIMIT = 5
DEFAULT_CHUNK_WORDS = 180
DEFAULT_CHUNK_OVERLAP_WORDS = 30
DEFAULT_HNSW_CONNECTIVITY = 16
DEFAULT_HNSW_EXPANSION_ADD = 128
DEFAULT_HNSW_EXPANSION_SEARCH = 64

MIN_SIMILARITY_THRESHOLD = 0.0
MAX_SIMILARITY_THRESHOLD = 1.0
MIN_LIMIT = 1
MIN_CHUNK_WORDS = 20
MIN_CHUNK_OVERLAP_WORDS = 0

DEFAULT_RECIPROCAL_MODE = "off"
VALID_RECIPROCAL_MODES = {"off", "audit"}

DEFAULT_CONNECTIONS_HEADING = "## Connections"
DEFAULT_ANCHOR_THRESHOLD = 0.72

DEFAULT_INCLUDE_GLOBS = ["**/*.md"]

CONFIG_LIST_FIELDS = (
    "target_paths",
    "include_globs",
    "exclude_globs",
    "exclude_name_contains",
    "exclude_files",
    "include_hidden",
    "exclude_sections",
)


@dataclass
class SemanticConfig:
    model: str = DEFAULT_MODEL
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    limit: int = DEFAULT_LIMIT
    chunk_words: int = DEFAULT_CHUNK_WORDS
    chunk_overlap_words: int = DEFAULT_CHUNK_OVERLAP_WORDS
    hnsw_connectivity: int = DEFAULT_HNSW_CONNECTIVITY
    hnsw_expansion_add: int = DEFAULT_HNSW_EXPANSION_ADD
    hnsw_expansion_search: int = DEFAULT_HNSW_EXPANSION_SEARCH


@dataclass
class AuditConfig:
    ghost_links: bool = True
    ambiguous_links: bool = True
    orphan_notes: bool = False
    reciprocal_mode: str = DEFAULT_RECIPROCAL_MODE


@dataclass
class AutoWriteConfig:
    enabled: bool = False
    connections_heading: str = DEFAULT_CONNECTIONS_HEADING
    anchor_threshold: float = DEFAULT_ANCHOR_THRESHOLD
    reciprocal: bool = False


@dataclass
class VaultConfig:
    target_paths: list[str] = field(default_factory=list)
    include_globs: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE_GLOBS))
    exclude_globs: list[str] = field(default_factory=list)
    exclude_name_contains: list[str] = field(default_factory=list)
    exclude_files: list[str] = field(default_factory=list)
    include_hidden: list[str] = field(default_factory=list)
    exclude_sections: list[str] = field(default_factory=list)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)
    audits: AuditConfig = field(default_factory=AuditConfig)
    auto_write: AutoWriteConfig = field(default_factory=AutoWriteConfig)

    def validate(self) -> None:
        for name in CONFIG_LIST_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} must be a list of strings")

        if any(Path(item).is_absolute() or ".." in Path(item).parts for item in self.target_paths):
            raise ValueError("target_paths must contain vault-relative paths without '..'")

        if not (MIN_SIMILARITY_THRESHOLD <= self.semantic.threshold <= MAX_SIMILARITY_THRESHOLD):
            raise ValueError("semantic.threshold must be between 0.0 and 1.0")

        if self.semantic.limit < MIN_LIMIT:
            raise ValueError("semantic.limit must be at least 1")

        if self.semantic.chunk_words < MIN_CHUNK_WORDS:
            raise ValueError("semantic.chunk_words must be at least 20")

        if self.semantic.chunk_overlap_words < MIN_CHUNK_OVERLAP_WORDS:
            raise ValueError("semantic.chunk_overlap_words cannot be negative")

        if self.semantic.chunk_overlap_words >= self.semantic.chunk_words:
            raise ValueError("chunk overlap must be smaller than chunk size")

        if self.audits.reciprocal_mode not in VALID_RECIPROCAL_MODES:
            raise ValueError("audits.reciprocal_mode must be 'off' or 'audit'")

        if not (MIN_SIMILARITY_THRESHOLD <= self.auto_write.anchor_threshold <= MAX_SIMILARITY_THRESHOLD):
            raise ValueError("auto_write.anchor_threshold must be between 0.0 and 1.0")

        if not self.auto_write.connections_heading.startswith("#"):
            raise ValueError("auto_write.connections_heading must be a Markdown heading")


def _merge_dataclass(instance: Any, values: dict[str, Any], section: str) -> Any:
    known = set(instance.__dataclass_fields__)
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"Unknown keys in {section}: {', '.join(sorted(unknown))}")
    for key, value in values.items():
        setattr(instance, key, value)
    return instance


def load_config(vault: Path, alternate: Path | None = None) -> VaultConfig:
    path = alternate or vault / CONFIG_NAME
    config = VaultConfig()
    if not path.exists():
        home_config = Path.home() / CONFIG_NAME
        if home_config.exists():
            path = home_config
        else:
            config.validate()
            return config
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    top_keys = set(VaultConfig.__dataclass_fields__)
    unknown = set(raw) - top_keys
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    for key in CONFIG_LIST_FIELDS:
        if key in raw:
            setattr(config, key, raw[key])
    if "semantic" in raw:
        _merge_dataclass(config.semantic, raw["semantic"] or {}, "semantic")
    if "audits" in raw:
        _merge_dataclass(config.audits, raw["audits"] or {}, "audits")
    if "auto_write" in raw:
        _merge_dataclass(config.auto_write, raw["auto_write"] or {}, "auto_write")
    config.validate()
    return config


def write_default_config(vault: Path, overwrite: bool = False, config: VaultConfig | None = None) -> Path:
    path = vault / CONFIG_NAME
    if path.exists() and not overwrite:
        return path
    config = config or VaultConfig()
    save_config(path, config)
    return path


def save_config(path: Path, config: VaultConfig) -> None:
    """Validate and save vault settings in a stable, human-readable format."""
    config.validate()
    header = (
        "# ZettelLinker vault settings\n"
        "# Automatic note editing is disabled until auto_write.enabled is true.\n"
    )
    payload = yaml.safe_dump(asdict(config), sort_keys=False, allow_unicode=True)
    try:
        path.write_text(header + payload, encoding="utf-8")
    except (PermissionError, OSError):
        user_home_config = Path.home() / CONFIG_NAME
        user_home_config.write_text(header + payload, encoding="utf-8")


GLOBAL_CONFIG_PATH = Path.home() / ".zettellinker_app.yml"


def get_last_vault_path() -> str:
    """Retrieve the last loaded vault directory path across app sessions."""
    if GLOBAL_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            val = str(data.get("last_vault_path", ""))
            if val and Path(val).exists():
                return val
        except Exception:
            pass
    return ""


def save_last_vault_path(vault_path: Path | str) -> None:
    """Persist the last loaded vault directory path for future app sessions."""
    try:
        resolved = str(Path(vault_path).resolve())
        data: dict[str, Any] = {}
        if GLOBAL_CONFIG_PATH.exists():
            data = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                data = {}
        data["last_vault_path"] = resolved
        GLOBAL_CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    except Exception:
        pass

