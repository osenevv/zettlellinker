from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from zettellinker.config import VaultConfig
from zettellinker.semantic import ExactIndex, SemanticEngine, USearchIndex, normalize
from zettellinker.vault import discover_notes


class FakeEmbedder:
    model_id = "fake"

    def encode(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vector = np.asarray(
                [
                    lowered.count("fruit") + lowered.count("apple") + lowered.count("banana"),
                    lowered.count("quantum") + lowered.count("physics"),
                    lowered.count("health") + lowered.count("nutrition"),
                    lowered.count("software"),
                ],
                dtype=np.float32,
            )
            if not vector.any():
                vector[3] = 1
            vectors.append(normalize(vector))
        return np.asarray(vectors, dtype=np.float32)


class MisleadingIndex(ExactIndex):
    """Returns reversed candidates and bogus scores to exercise exact reranking."""

    def search(self, vector, count):
        keys, _distances = super().search(vector, count)
        reversed_keys = keys[::-1].copy()
        bogus_distances = np.zeros(len(reversed_keys), dtype=np.float32)
        return reversed_keys, bogus_distances


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        (self.vault / "Apple.md").write_text("Fruit nutrition supports health.", encoding="utf-8")
        (self.vault / "Banana.md").write_text("Fruit health and nutrition.", encoding="utf-8")
        (self.vault / "Quantum.md").write_text("Quantum physics research.", encoding="utf-8")
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.7

    def tearDown(self):
        self.temp.cleanup()

    def engine(self):
        return SemanticEngine(
            self.vault,
            self.config,
            embedder=FakeEmbedder(),
            index_factory=ExactIndex,
        )

    def test_incremental_cache_and_similarity(self):
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            first = self.engine()
            stats = first.refresh(discover_notes(self.vault, self.config))
            self.assertEqual(stats["embedded"], 3)
            suggestions = first.suggestions_for("Apple")
            self.assertEqual([item.target for item in suggestions], ["Banana"])

            second = self.engine()
            stats = second.refresh(discover_notes(self.vault, self.config))
            self.assertEqual(stats["embedded"], 0)
            self.assertEqual(stats["reused"], 3)

            (self.vault / "Banana.md").write_text("Quantum physics only.", encoding="utf-8")
            third = self.engine()
            stats = third.refresh(discover_notes(self.vault, self.config))
            self.assertEqual(stats["embedded"], 1)
            self.assertEqual(third.suggestions_for("Apple"), [])

    def test_usearch_build_search_and_reload(self):
        index_path = Path(self.temp.name) / "index.usearch"
        index = USearchIndex(self.config.semantic)
        vectors = np.asarray([[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        vectors = np.asarray([normalize(vector) for vector in vectors])
        index.build(np.asarray([1, 2, 3], dtype=np.uint64), vectors)
        index.save(index_path)
        loaded = USearchIndex(self.config.semantic)
        loaded.load(index_path, 4)
        keys, _distances = loaded.search(vectors[0], 2)
        self.assertEqual(keys.tolist(), [1, 2])

    def test_hnsw_candidates_are_reranked_with_exact_cosine(self):
        (self.vault / "Pear.md").write_text("Fruit.", encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = SemanticEngine(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=MisleadingIndex,
            )
            engine.refresh(discover_notes(self.vault, self.config))

            suggestions = engine.suggestions_for("Apple", limit=3, threshold=0.1)

        self.assertEqual([item.target for item in suggestions], ["Banana", "Pear"])
        self.assertGreater(suggestions[0].score, suggestions[1].score)
