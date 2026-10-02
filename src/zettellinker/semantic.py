from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

import numpy as np
from platformdirs import user_cache_path

from .config import SemanticConfig, VaultConfig
from .models import IndexStats, Note, Suggestion
from .vault import VaultGraph


CACHE_VERSION = 1
DEFAULT_VECTOR_DIMENSION = 384
DEFAULT_BATCH_SIZE = 32
MIN_WORD_WEIGHT = 1
CANDIDATE_SEARCH_MULTIPLIER = 8
MIN_CANDIDATES = 32
KEY_HASH_LENGTH = 20
SCORE_DECIMAL_PRECISION = 6

Progress = Callable[[str], None]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vault_key(vault: Path) -> str:
    return hashlib.sha256(str(vault.resolve()).encode("utf-8")).hexdigest()[:KEY_HASH_LENGTH]


def cache_directory(vault: Path) -> Path:
    return Path(user_cache_path("zettellinker", "zettellinker")) / "vaults" / _vault_key(vault)


def model_marker_path(model: str) -> Path:
    key = hashlib.sha256(model.encode("utf-8")).hexdigest()[:KEY_HASH_LENGTH]
    return Path(user_cache_path("zettellinker", "zettellinker")) / "models" / f"{key}.confirmed"


def model_download_confirmed(model: str) -> bool:
    return model_marker_path(model).exists()


def mark_model_downloaded(model: str) -> None:
    marker = model_marker_path(model)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(model + "\n", encoding="utf-8")


def semantic_signature(config: SemanticConfig, excluded_sections: list[str]) -> str:
    payload = {
        "model": config.model,
        "chunk_words": config.chunk_words,
        "chunk_overlap_words": config.chunk_overlap_words,
        "excluded_sections": excluded_sections,
    }
    return _hash_text(json.dumps(payload, sort_keys=True))


def chunk_text(text: str, chunk_words: int, overlap_words: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_words:
        return [" ".join(words)]
    step = chunk_words - overlap_words
    return [" ".join(words[start : start + chunk_words]) for start in range(0, len(words), step)]


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    magnitude = float(np.linalg.norm(vector))
    if magnitude == 0:
        return vector
    return vector / magnitude


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    """Return exact cosine similarity for two dense vectors."""
    left_normalized = normalize(left)
    right_normalized = normalize(right)
    if left_normalized.shape != right_normalized.shape:
        raise ValueError("Cannot compare embeddings with different dimensions")
    return float(np.dot(left_normalized, right_normalized))


class Embedder(Protocol):
    model_id: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is unavailable; reinstall ZettelLinker"
            ) from exc
        self._model = SentenceTransformer(self.model_id)
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, DEFAULT_VECTOR_DIMENSION), dtype=np.float32)
        vectors = self._load().encode(
            list(texts),
            batch_size=DEFAULT_BATCH_SIZE,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32)


def embed_note(note: Note, embedder: Embedder, config: SemanticConfig) -> np.ndarray | None:
    chunks = chunk_text(note.clean_text, config.chunk_words, config.chunk_overlap_words)
    if not chunks:
        return None
    vectors = embedder.encode(chunks)
    if len(vectors) == 0:
        return None
    weights = np.asarray([max(MIN_WORD_WEIGHT, len(chunk.split())) for chunk in chunks], dtype=np.float32)
    combined = np.average(vectors, axis=0, weights=weights)
    return normalize(combined)


@dataclass
class CacheEntry:
    identity: str
    relative_path: str
    mtime_ns: int
    size: int
    content_hash: str
    vector: list[float]
    key: int


class SemanticCache:
    def __init__(self, vault: Path):
        self.directory = cache_directory(vault)
        self.manifest_path = self.directory / "manifest.json"
        self.index_path = self.directory / "index.npz"
        self.signature = ""
        self.index_fingerprint = ""
        self.entries: dict[str, CacheEntry] = {}

    def load(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if payload.get("version") != CACHE_VERSION:
                return
            self.signature = payload.get("signature", "")
            self.index_fingerprint = payload.get("index_fingerprint", "")
            self.entries = {
                identity: CacheEntry(**entry)
                for identity, entry in (payload.get("entries") or {}).items()
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.signature = ""
            self.index_fingerprint = ""
            self.entries = {}

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "signature": self.signature,
            "index_fingerprint": self.index_fingerprint,
            "entries": {identity: asdict(entry) for identity, entry in self.entries.items()},
        }
        _atomic_text(self.manifest_path, json.dumps(payload, separators=(",", ":")))


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SearchIndex(Protocol):
    def build(self, keys: np.ndarray, vectors: np.ndarray) -> None: ...
    def load(self, path: Path, dimensions: int) -> None: ...
    def save(self, path: Path) -> None: ...
    def search(self, vector: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]: ...


class USearchIndex:
    def __init__(self, config: SemanticConfig):
        self.config = config
        self.index = None

    def _new(self, dimensions: int):
        try:
            from usearch.index import Index
        except ImportError as exc:
            raise RuntimeError("USearch is unavailable; reinstall ZettelLinker") from exc
        return Index(
            ndim=dimensions,
            metric="cos",
            dtype="f32",
            connectivity=self.config.hnsw_connectivity,
            expansion_add=self.config.hnsw_expansion_add,
            expansion_search=self.config.hnsw_expansion_search,
        )

    def build(self, keys: np.ndarray, vectors: np.ndarray) -> None:
        if not len(vectors):
            self.index = None
            return
        self.index = self._new(vectors.shape[1])
        self.index.add(keys, vectors)

    def load(self, path: Path, dimensions: int) -> None:
        self.index = self._new(dimensions)
        self.index.load(str(path))

    def save(self, path: Path) -> None:
        if self.index is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        self.index.save(str(temporary))
        os.replace(temporary, path)

    def search(self, vector: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
        if self.index is None or count <= 0:
            return np.asarray([], dtype=np.int64), np.asarray([], dtype=np.float32)
        matches = self.index.search(vector, count)
        return np.asarray(matches.keys), np.asarray(matches.distances, dtype=np.float32)


class ExactIndex:
    """Exact cosine search. Default for a vault. USearch is the large-vault option."""

    def __init__(self, _config: SemanticConfig):
        self.keys = np.asarray([], dtype=np.int64)
        self.vectors = np.empty((0, 0), dtype=np.float32)

    def build(self, keys: np.ndarray, vectors: np.ndarray) -> None:
        self.keys, self.vectors = keys.copy(), vectors.copy()

    def load(self, path: Path, dimensions: int) -> None:
        with path.open("rb") as handle:
            payload = np.load(handle)
            self.keys, self.vectors = payload["keys"], payload["vectors"]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as handle:
            np.savez(handle, keys=self.keys, vectors=self.vectors)
        os.replace(temporary, path)

    def search(self, vector: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
        if not len(self.keys):
            return np.asarray([], dtype=np.int64), np.asarray([], dtype=np.float32)
        similarities = self.vectors @ normalize(vector)
        order = np.argsort(-similarities)[:count]
        return self.keys[order], 1.0 - similarities[order]


class DeterministicTestEmbedder:
    model_id = "test-deterministic"

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            lowered = text.lower()
            v = np.asarray([
                lowered.count("fruit") + lowered.count("apple") + lowered.count("banana"),
                lowered.count("quantum") + lowered.count("physics"),
                lowered.count("health") + lowered.count("nutrition"),
                lowered.count("software") + lowered.count("ideas") + lowered.count("one") + lowered.count("two"),
            ], dtype=np.float32)
            if not v.any():
                v[3] = 1.0
            vectors.append(normalize(v))
        return np.asarray(vectors, dtype=np.float32)


class SemanticEngine:
    def __init__(
        self,
        vault: Path,
        config: VaultConfig,
        embedder: Embedder | None = None,
        index_factory: type[SearchIndex] | None = None,
        progress: Progress | None = None,
    ):
        self.vault = vault.resolve()
        self.config = config
        if embedder is not None:
            self.embedder = embedder
        elif os.environ.get("ZETTELLINKER_TEST_EMBEDDER") == "1":
            self.embedder = DeterministicTestEmbedder()
        else:
            self.embedder = SentenceTransformerEmbedder(config.semantic.model)
        self.cache = SemanticCache(self.vault)
        if index_factory is not None:
            self.index = index_factory(config.semantic)
        elif config.semantic.index == "usearch":
            self.index = USearchIndex(config.semantic)
        else:
            self.index = ExactIndex(config.semantic)
        if isinstance(self.index, USearchIndex):
            self.cache.index_path = self.cache.directory / "index.usearch"
        self.progress = progress or (lambda _message: None)
        self.graph: VaultGraph | None = None

    def refresh(self, notes: list[Note], graph: VaultGraph | None = None) -> IndexStats:
        self.graph = graph if graph is not None else VaultGraph(notes)
        self.cache.load()
        signature = semantic_signature(self.config.semantic, self.config.exclude_sections)
        if self.cache.signature != signature:
            self.cache.entries = {}
            self.cache.index_fingerprint = ""
        self.cache.signature = signature
        note_by_identity = {note.identity: note for note in notes}
        removed = set(self.cache.entries) - set(note_by_identity)
        for identity in removed:
            del self.cache.entries[identity]

        changed: list[Note] = []
        reused = 0
        for note in notes:
            stat = note.path.stat()
            entry = self.cache.entries.get(note.identity)
            if entry and entry.mtime_ns == stat.st_mtime_ns and entry.size == stat.st_size:
                reused += 1
                continue
            content_hash = _hash_text(note.clean_text)
            if entry and entry.content_hash == content_hash:
                entry.mtime_ns, entry.size = stat.st_mtime_ns, stat.st_size
                reused += 1
            else:
                changed.append(note)

        next_key = max((entry.key for entry in self.cache.entries.values()), default=-1) + 1
        for position, note in enumerate(changed, 1):
            self.progress(f"Embedding {position}/{len(changed)}: {note.relative_path}")
            vector = embed_note(note, self.embedder, self.config.semantic)
            stat = note.path.stat()
            if vector is None:
                self.cache.entries.pop(note.identity, None)
                continue
            old = self.cache.entries.get(note.identity)
            key = old.key if old else next_key
            if old is None:
                next_key += 1
            self.cache.entries[note.identity] = CacheEntry(
                identity=note.identity,
                relative_path=note.relative_path,
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
                content_hash=_hash_text(note.clean_text),
                vector=vector.astype(float).tolist(),
                key=key,
            )

        fingerprint = _hash_text(
            "\n".join(
                f"{identity}:{entry.content_hash}:{entry.key}"
                for identity, entry in sorted(self.cache.entries.items())
            )
        )
        usable_index = (
            fingerprint == self.cache.index_fingerprint
            and self.cache.index_path.exists()
            and bool(self.cache.entries)
        )
        if usable_index:
            dimensions = len(next(iter(self.cache.entries.values())).vector)
            try:
                self.index.load(self.cache.index_path, dimensions)
            except Exception:
                usable_index = False
        if not usable_index:
            entries = sorted(self.cache.entries.values(), key=lambda entry: entry.key)
            keys = np.asarray([entry.key for entry in entries], dtype=np.uint64)
            vectors = np.asarray([entry.vector for entry in entries], dtype=np.float32)
            self.index.build(keys, vectors)
            self.index.save(self.cache.index_path)
            self.cache.index_fingerprint = fingerprint
        self.cache.save()
        return IndexStats(embedded=len(changed), reused=reused, removed=len(removed))

    def suggestions_for(
        self,
        identity: str,
        limit: int | None = None,
        threshold: float | None = None,
    ) -> list[Suggestion]:
        if self.graph is None:
            raise RuntimeError("SemanticEngine.refresh must run before searching")
        entry = self.cache.entries.get(identity)
        if entry is None:
            return []
        limit = limit or self.config.semantic.limit
        threshold = self.config.semantic.threshold if threshold is None else threshold
        key_to_identity = {item.key: item.identity for item in self.cache.entries.values()}
        requested = min(len(key_to_identity), max(limit * CANDIDATE_SEARCH_MULTIPLIER, MIN_CANDIDATES))
        keys, _ = self.index.search(
            np.asarray(entry.vector, dtype=np.float32), requested
        )

        candidates: list[tuple[str, float]] = []
        seen: set[str] = set()
        source_vector = np.asarray(entry.vector, dtype=np.float32)
        for key in keys.tolist():
            target = key_to_identity.get(int(key))
            if (
                not target
                or target in seen
                or target == identity
                or self.graph.connected(identity, target)
            ):
                continue
            target_entry = self.cache.entries.get(target)
            if target_entry is None:
                continue
            seen.add(target)
            score = cosine_similarity(
                source_vector,
                np.asarray(target_entry.vector, dtype=np.float32),
            )
            if score < threshold:
                continue
            candidates.append((target, score))

        candidates.sort(
            key=lambda candidate: (-candidate[1], candidate[0].casefold(), candidate[0])
        )
        return [
            Suggestion(identity, target, round(score, SCORE_DECIMAL_PRECISION))
            for target, score in candidates[:limit]
        ]

    def all_suggestions(self) -> list[Suggestion]:
        if self.graph is None:
            raise RuntimeError("SemanticEngine.refresh must run before searching")
        output: list[Suggestion] = []
        for note in self.graph.notes:
            output.extend(self.suggestions_for(note.identity))
        return output

    def vector_for(self, identity: str) -> np.ndarray | None:
        entry = self.cache.entries.get(identity)
        if not entry:
            return None
        return np.asarray(entry.vector, dtype=np.float32)

