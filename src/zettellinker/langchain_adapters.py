from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore
from pydantic import Field

from .config import SemanticConfig
from .models import Note, Suggestion, WikiLink
from .semantic import (
    CANDIDATE_SEARCH_MULTIPLIER,
    DEFAULT_VECTOR_DIMENSION,
    MIN_CANDIDATES,
    SCORE_DECIMAL_PRECISION,
    Embedder,
    SemanticEngine,
    cosine_similarity,
    embed_note,
    normalize,
)
from .vault import VaultGraph


def note_to_document(note: Note) -> Document:
    """Convert a ZettelLinker Note domain model to a LangChain Document."""
    return Document(
        page_content=note.clean_text,
        metadata={
            "identity": note.identity,
            "relative_path": note.relative_path,
            "basename": note.basename,
            "path": str(note.path),
            "raw_text": note.text,
            "links": [
                {
                    "raw": link.raw,
                    "target": link.target,
                    "alias": link.alias,
                    "fragment": link.fragment,
                    "embed": link.embed,
                    "start": link.start,
                    "end": link.end,
                }
                for link in note.links
            ],
            "link_targets": [link.target for link in note.links],
        },
    )


def document_to_note(doc: Document) -> Note:
    """Reconstruct a ZettelLinker Note from a LangChain Document."""
    meta = doc.metadata or {}
    raw_links = meta.get("links", [])
    parsed_links = [
        WikiLink(
            raw=item["raw"],
            target=item["target"],
            alias=item.get("alias"),
            fragment=item.get("fragment"),
            embed=item.get("embed", False),
            start=item["start"],
            end=item["end"],
        )
        for item in raw_links
    ]
    raw_path = meta.get("path")
    path = Path(raw_path) if raw_path else Path(meta.get("relative_path", "unknown.md"))
    return Note(
        path=path,
        relative_path=meta.get("relative_path", path.name),
        identity=meta.get("identity", path.stem),
        basename=meta.get("basename", path.name),
        text=meta.get("raw_text", doc.page_content),
        clean_text=doc.page_content,
        links=parsed_links,
    )


class LangChainEmbedder(Embeddings):
    """LangChain Embeddings wrapper around ZettelLinker's embedder."""

    def __init__(self, embedder: Embedder | Any):
        self.underlying = embedder
        self.model_id = getattr(embedder, "model_id", "custom")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if hasattr(self.underlying, "encode"):
            vectors = self.underlying.encode(texts)
            return [np.asarray(v, dtype=float).tolist() for v in vectors]
        if hasattr(self.underlying, "embed_documents"):
            return self.underlying.embed_documents(texts)
        raise AttributeError(f"Underlying embedder {type(self.underlying)} has neither encode nor embed_documents")

    def embed_query(self, text: str) -> list[float]:
        if not text:
            return [0.0] * DEFAULT_VECTOR_DIMENSION
        if hasattr(self.underlying, "encode"):
            vectors = self.underlying.encode([text])
            if len(vectors) == 0:
                return [0.0] * DEFAULT_VECTOR_DIMENSION
            return np.asarray(vectors[0], dtype=float).tolist()
        if hasattr(self.underlying, "embed_query"):
            return self.underlying.embed_query(text)
        return self.embed_documents([text])[0]


class ZettelVaultRetriever(BaseRetriever):
    """LangChain retriever for an Obsidian vault with graph-aware link filtering."""

    vectorstore: Any = Field(description="ZettelVectorStore instance")
    k: int = Field(default=5, description="Number of suggestions to retrieve")
    threshold: float = Field(default=0.6, description="Minimum cosine similarity")
    source_identity: str | None = Field(default=None, description="Source note identity to exclude or compare against")
    exclude_connected: bool = Field(default=True, description="Filter out existing graph connections")

    def _get_relevant_documents(self, query: str, *, run_manager: Any = None) -> list[Document]:
        source_id = self.source_identity or query
        matches = self.vectorstore.similarity_search_for_identity(
            identity=source_id,
            limit=self.k,
            threshold=self.threshold,
            exclude_connected=self.exclude_connected,
        )
        docs: list[Document] = []
        for target_doc, score in matches:
            target_doc.metadata["score"] = score
            target_doc.metadata["source"] = source_id
            docs.append(target_doc)
        return docs


class LangChainZettelVectorStore(VectorStore):
    """LangChain VectorStore implementation backed by ZettelLinker SemanticEngine."""

    def __init__(self, engine: SemanticEngine, embeddings: Embeddings | None = None):
        self.engine = engine
        self._embeddings = embeddings or LangChainEmbedder(engine.embedder)

    @property
    def embeddings(self) -> Embeddings:
        return self._embeddings

    def add_texts(
        self,
        texts: Sequence[str],
        metadatas: Sequence[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        raise NotImplementedError("ZettelVectorStore uses vault notes for indexing via refresh()")

    def add_documents(self, documents: Sequence[Document], **kwargs: Any) -> list[str]:
        notes = [document_to_note(doc) for doc in documents]
        self.engine.refresh(notes)
        return [note.identity for note in notes]

    def similarity_search_by_vector_with_score(
        self,
        embedding: list[float],
        k: int = 4,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]:
        if not self.engine.cache.entries:
            return []
        query_vec = normalize(np.asarray(embedding, dtype=np.float32))
        key_to_identity = {item.key: item.identity for item in self.engine.cache.entries.values()}
        requested = min(len(key_to_identity), max(k * CANDIDATE_SEARCH_MULTIPLIER, MIN_CANDIDATES))
        keys, _ = self.engine.index.search(query_vec, requested)

        candidates: list[tuple[str, float]] = []
        seen: set[str] = set()
        for key in keys.tolist():
            identity = key_to_identity.get(int(key))
            if not identity or identity in seen:
                continue
            entry = self.engine.cache.entries.get(identity)
            if entry is None:
                continue
            seen.add(identity)
            score = cosine_similarity(query_vec, np.asarray(entry.vector, dtype=np.float32))
            candidates.append((identity, score))

        candidates.sort(key=lambda c: (-c[1], c[0].casefold(), c[0]))
        results: list[tuple[Document, float]] = []
        for identity, score in candidates[:k]:
            note = self.engine.graph.by_identity.get(identity) if self.engine.graph else None
            if note:
                doc = note_to_document(note)
            else:
                entry = self.engine.cache.entries[identity]
                doc = Document(
                    page_content="",
                    metadata={"identity": identity, "relative_path": entry.relative_path},
                )
            results.append((doc, round(score, SCORE_DECIMAL_PRECISION)))
        return results

    def similarity_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        **kwargs: Any,
    ) -> list[Document]:
        matches = self.similarity_search_by_vector_with_score(embedding, k=k, **kwargs)
        return [doc for doc, _score in matches]

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> list[Document]:
        matches = self.similarity_search_with_score(query, k=k, **kwargs)
        return [doc for doc, _score in matches]

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]:
        vec = self.embeddings.embed_query(query)
        return self.similarity_search_by_vector_with_score(vec, k=k, **kwargs)

    def similarity_search_for_identity(
        self,
        identity: str,
        limit: int | None = None,
        threshold: float | None = None,
        exclude_connected: bool = True,
    ) -> list[tuple[Document, float]]:
        """Find candidate documents for a specific note identity, with graph topology filtering."""
        entry = self.engine.cache.entries.get(identity)
        if entry is None:
            return []
        limit = limit or self.engine.config.semantic.limit
        threshold = self.engine.config.semantic.threshold if threshold is None else threshold
        raw_suggestions = self.engine.suggestions_for(identity, limit=limit, threshold=threshold)

        results: list[tuple[Document, float]] = []
        for s in raw_suggestions:
            note = self.engine.graph.by_identity.get(s.target) if self.engine.graph else None
            if note:
                doc = note_to_document(note)
            else:
                target_entry = self.engine.cache.entries.get(s.target)
                rel_path = target_entry.relative_path if target_entry else s.target
                doc = Document(page_content="", metadata={"identity": s.target, "relative_path": rel_path})
            results.append((doc, s.score))
        return results

    def as_retriever(self, **kwargs: Any) -> ZettelVaultRetriever:
        return ZettelVaultRetriever(
            vectorstore=self,
            k=kwargs.get("k", self.engine.config.semantic.limit),
            threshold=kwargs.get("threshold", self.engine.config.semantic.threshold),
            source_identity=kwargs.get("source_identity"),
            exclude_connected=kwargs.get("exclude_connected", True),
        )

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LangChainZettelVectorStore:
        raise NotImplementedError("Use from_engine or initialize with an existing SemanticEngine.")

    @classmethod
    def from_engine(
        cls,
        engine: SemanticEngine,
        embeddings: Embeddings | None = None,
    ) -> LangChainZettelVectorStore:
        return cls(engine=engine, embeddings=embeddings)
