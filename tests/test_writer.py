from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zettellinker.config import VaultConfig
from zettellinker.models import Suggestion
from zettellinker.semantic import ExactIndex, SemanticEngine
from zettellinker.vault import discover_notes, parse_wikilinks
from zettellinker.writer import LinkWriter, undo_last

from test_semantic import FakeEmbedder


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.5
        self.config.auto_write.enabled = True
        self.config.auto_write.anchor_threshold = 0.6

    def tearDown(self):
        self.temp.cleanup()

    def make_engine(self):
        engine = SemanticEngine(
            self.vault,
            self.config,
            embedder=FakeEmbedder(),
            index_factory=ExactIndex,
        )
        engine.refresh(discover_notes(self.vault, self.config))
        return engine

    def test_inline_write_and_undo(self):
        apple = self.vault / "Apple.md"
        apple.write_text("Fruit nutrition supports long term health.", encoding="utf-8")
        (self.vault / "Banana.md").write_text("Fruit nutrition and health benefits.", encoding="utf-8")
        before = apple.read_text(encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = self.make_engine()
            result = LinkWriter(engine).apply([Suggestion("Apple", "Banana", 0.95)], source_only="Apple")
            self.assertEqual(result.links_added, 1)
            self.assertIn("[[Banana|", apple.read_text(encoding="utf-8"))
            undone = undo_last(self.vault)
            self.assertEqual(undone.modified, ("Apple",))
            self.assertEqual(apple.read_text(encoding="utf-8"), before)

    def test_connections_fallback_uses_bare_link(self):
        note = self.vault / "A.md"
        note.write_text("Tiny", encoding="utf-8")
        (self.vault / "B.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = self.make_engine()
            LinkWriter(engine).apply([Suggestion("A", "B", 0.9)], source_only="A")
        output = note.read_text(encoding="utf-8")
        self.assertIn("## Connections", output)
        self.assertIn("\n[[B]]\n", output)
        self.assertNotIn("- [[B]]", output)

    def test_undo_refuses_when_file_changed_after_write(self):
        note = self.vault / "A.md"
        note.write_text("Tiny", encoding="utf-8")
        (self.vault / "B.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = self.make_engine()
            LinkWriter(engine).apply([Suggestion("A", "B", 0.9)], source_only="A")
            note.write_text(note.read_text(encoding="utf-8") + "User edit\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed after"):
                undo_last(self.vault)

    def test_inline_write_never_nests_wikilinks(self):
        note = self.vault / "A.md"
        note.write_text("Fruit nutrition supports long term health.", encoding="utf-8")
        (self.vault / "B.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        (self.vault / "C.md").write_text("Fruit nutrition supports health.", encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = self.make_engine()
            result = LinkWriter(engine).apply(
                [Suggestion("A", "B", 0.95), Suggestion("A", "C", 0.94)],
                source_only="A",
            )

        output = note.read_text(encoding="utf-8")
        self.assertEqual(result.links_added, 2)
        self.assertNotIn("|[[", output)
        self.assertEqual(len(parse_wikilinks(output)), 2)

    def test_markdown_links_and_images_are_not_wrapped(self):
        note = self.vault / "A.md"
        original = "[Fruit nutrition supports health](https://example.com) ![health image](health.png)"
        note.write_text(original, encoding="utf-8")
        (self.vault / "B.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            engine = self.make_engine()
            LinkWriter(engine).apply(
                [Suggestion("A", "B", 0.95)],
                source_only="A",
            )

        output = note.read_text(encoding="utf-8")
        self.assertIn(original, output)
        self.assertIn("\n[[B]]\n", output)
