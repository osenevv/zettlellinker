from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document

from zettellinker.config import VaultConfig
from zettellinker.langchain_adapters import (
    LangChainEmbedder,
    LangChainZettelVectorStore,
    ZettelVaultRetriever,
    document_to_note,
    note_to_document,
)
from zettellinker.models import Note, WikiLink
from zettellinker.semantic import ExactIndex, SemanticEngine
from zettellinker.vault import discover_notes

from test_semantic import FakeEmbedder


class LangChainAdaptersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        (self.vault / "Apple.md").write_text("Fruit nutrition supports health. [[Banana]]", encoding="utf-8")
        (self.vault / "Banana.md").write_text("Fruit health and nutrition benefits.", encoding="utf-8")
        (self.vault / "Quantum.md").write_text("Quantum physics research.", encoding="utf-8")
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.5

    def tearDown(self):
        self.temp.cleanup()

    def test_note_and_document_conversion_roundtrip(self):
        note = Note(
            path=self.vault / "Test.md",
            relative_path="Folder/Test.md",
            identity="Folder/Test",
            basename="Test",
            text="Original text [[Target|Alias]]",
            clean_text="Original text Alias",
            links=[
                WikiLink(
                    raw="[[Target|Alias]]",
                    target="Target",
                    alias="Alias",
                    fragment=None,
                    embed=False,
                    start=14,
                    end=30,
                )
            ],
        )
        doc = note_to_document(note)
        self.assertIsInstance(doc, Document)
        self.assertEqual(doc.page_content, note.clean_text)
        self.assertEqual(doc.metadata["identity"], "Folder/Test")
        self.assertEqual(doc.metadata["basename"], "Test")
        self.assertEqual(doc.metadata["link_targets"], ["Target"])
        reconstructed = document_to_note(doc)
        self.assertEqual(reconstructed.identity, note.identity)
        self.assertEqual(reconstructed.clean_text, note.clean_text)
        self.assertEqual(reconstructed.text, note.text)
        self.assertEqual(len(reconstructed.links), 1)
        self.assertEqual(reconstructed.links[0].target, "Target")
        self.assertEqual(reconstructed.links[0].alias, "Alias")

    def test_langchain_embedder(self):
        embedder = FakeEmbedder()
        lc_embedder = LangChainEmbedder(embedder)
        texts = ["Fruit nutrition", "Quantum physics"]
        vecs = lc_embedder.embed_documents(texts)
        self.assertEqual(len(vecs), 2)
        self.assertEqual(len(vecs[0]), 4)
        query_vec = lc_embedder.embed_query("Fruit")
        self.assertEqual(len(query_vec), 4)
        self.assertGreater(query_vec[0], 0.0)

    def test_langchain_vectorstore_similarity_search(self):
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = SemanticEngine(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            notes = discover_notes(self.vault, self.config)
            engine.refresh(notes)
            vs = LangChainZettelVectorStore.from_engine(engine)
            results = vs.similarity_search("Fruit health", k=2)
            self.assertGreaterEqual(len(results), 1)
            identities = [doc.metadata["identity"] for doc in results]
            self.assertIn("Apple", identities)
            with_scores = vs.similarity_search_with_score("Fruit health", k=1)
            doc, score = with_scores[0]
            self.assertEqual(doc.metadata["identity"], "Apple")
            self.assertIsInstance(score, float)
            self.assertGreater(score, 0.5)

    def test_zettel_vault_retriever_filters_existing_links(self):
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = SemanticEngine(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            notes = discover_notes(self.vault, self.config)
            engine.refresh(notes)
            vs = LangChainZettelVectorStore.from_engine(engine)
            retriever = vs.as_retriever(source_identity="Apple", k=5, threshold=0.1)
            self.assertIsInstance(retriever, ZettelVaultRetriever)
            docs = retriever.invoke("Apple")
            target_ids = [doc.metadata["identity"] for doc in docs]
            self.assertNotIn("Banana", target_ids)
