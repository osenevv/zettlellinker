from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zettellinker.config import VaultConfig
from zettellinker.models import WikiLink
from zettellinker.semantic import ExactIndex, SemanticEngine
from zettellinker.vault import (
    VaultGraph,
    clean_for_embedding,
    discover_notes,
    normalize_identity,
    parse_wikilinks,
)
from zettellinker.workflow import run_vault_audit, run_vault_scan
from zettellinker.writer import LinkWriter, undo_last

from test_semantic import FakeEmbedder


class VaultEdgeCasesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.5
        self.config.auto_write.anchor_threshold = 0.6

    def tearDown(self):
        self.temp.cleanup()

    def test_unicode_emoji_and_international_filenames(self):
        """Vault with emoji, Cyrillic, German umlauts, and spaces in filenames."""
        (self.vault / "💡 Ideas & Brainstorming.md").write_text(
            "Fruit nutrition supports health and mental energy.", encoding="utf-8"
        )
        (self.vault / "🍎 Здоровое Питание.md").write_text(
            "Fruit nutrition and health benefits for the body.", encoding="utf-8"
        )
        (self.vault / "Überblick (Vorschau).md").write_text(
            "Quantum physics principles.", encoding="utf-8"
        )

        notes = discover_notes(self.vault, self.config)
        identities = {note.identity for note in notes}
        self.assertIn("💡 Ideas & Brainstorming", identities)
        self.assertIn("🍎 Здоровое Питание", identities)
        self.assertIn("Überblick (Vorschau)", identities)

        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            suggestions = state.get("suggestions", [])
            # The two fruit notes should match despite emoji and Cyrillic names
            pairs = {(s.source, s.target) for s in suggestions}
            self.assertTrue(
                ("💡 Ideas & Brainstorming", "🍎 Здоровое Питание") in pairs
                or ("🍎 Здоровое Питание", "💡 Ideas & Brainstorming") in pairs
            )

    def test_zero_byte_and_whitespace_only_notes(self):
        """0-byte and whitespace-only notes should be discovered but not crash embedding or indexing."""
        (self.vault / "Empty.md").write_text("", encoding="utf-8")
        (self.vault / "Spaces.md").write_text("   \n\t\n   ", encoding="utf-8")
        (self.vault / "Normal.md").write_text("Fruit nutrition and health.", encoding="utf-8")

        notes = discover_notes(self.vault, self.config)
        self.assertEqual(len(notes), 3)

        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            # Embedding should succeed without division by zero or empty array errors
            stats = state.get("index_stats")
            self.assertIsNotNone(stats)
            self.assertEqual(stats.embedded, 3)
            # Only Normal.md has vector entries in cache; empty and whitespace notes are skipped
            self.assertEqual(len(state["engine"].cache.entries), 1)
            self.assertIn("Normal", state["engine"].cache.entries)

    def test_latex_math_blocks_and_inline_math_are_protected(self):
        """LaTeX $$ ... $$ and $ ... $ should never have wikilinks parsed inside or anchored into."""
        content = """# Physics Note
Here is some math:
$$
\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}
$$
And inline math $f(x) = x^2$ is here.
Fruit nutrition supports health outside of math equations.
"""
        note_path = self.vault / "Math.md"
        note_path.write_text(content, encoding="utf-8")
        (self.vault / "Nutrition.md").write_text("Fruit nutrition and health.", encoding="utf-8")

        links = parse_wikilinks(content)
        self.assertEqual(len(links), 0)

        cleaned = clean_for_embedding(content, [])
        self.assertIn("Fruit nutrition supports health", cleaned)

    def test_obsidian_callouts_and_block_references(self):
        """Obsidian callouts (> [!NOTE]) and block references (^block-id) should parse cleanly."""
        content = """# Architecture
> [!NOTE]
> This is a crucial observation about [[TargetNote]].

Some discussion paragraph with an anchor phrase fruit nutrition supports health. ^block-a1b2
"""
        links = parse_wikilinks(content)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "TargetNote")

    def test_attachments_and_canvas_files_are_ignored(self):
        """Files like .png, .pdf, .canvas, .mp3 must be ignored from markdown discovery and treated as attachments."""
        (self.vault / "Doc.md").write_text("Link to ![[diagram.png]] and ![[paper.pdf]] and ![[mindmap.canvas]].", encoding="utf-8")
        (self.vault / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.vault / "paper.pdf").write_bytes(b"%PDF-1.5")
        (self.vault / "mindmap.canvas").write_text('{"nodes":[]}', encoding="utf-8")

        notes = discover_notes(self.vault, self.config)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].identity, "Doc")

        graph = VaultGraph(notes)
        # All attachment links should be classified as attachments, not ghost links
        ghost_findings = [f for f in graph.findings(self.config) if f.kind == "ghost_link"]
        self.assertEqual(len(ghost_findings), 0)

    def test_unclosed_frontmatter_falls_back_gracefully(self):
        """Malformed frontmatter missing the closing delimiter should not crash discovery."""
        malformed = "---\ntitle: Broken Note\nFruit nutrition supports health."
        (self.vault / "Broken.md").write_text(malformed, encoding="utf-8")

        notes = discover_notes(self.vault, self.config)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].identity, "Broken")
        self.assertIn("Fruit nutrition supports health", notes[0].clean_text)

    def test_wikilinks_inside_yaml_frontmatter_are_ignored(self):
        """Wikilinks inside YAML frontmatter must not be parsed into graph edges."""
        content = """---
tags: [knowledge]
related: "[[IgnoredNote]]"
---
# Main Note
Actual body text [[RealNote]].
"""
        links = parse_wikilinks(content)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "RealNote")

    def test_deeply_nested_directory_structures(self):
        """Vault with 5 levels of nested folders should resolve relative paths and identities correctly."""
        deep_folder = self.vault / "Area" / "Sub1" / "Sub2" / "Sub3" / "DeepNotes"
        deep_folder.mkdir(parents=True)
        deep_note = deep_folder / "CoreIdea.md"
        deep_note.write_text("Deeply nested note content.", encoding="utf-8")

        notes = discover_notes(self.vault, self.config)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].identity, "Area/Sub1/Sub2/Sub3/DeepNotes/CoreIdea")
        self.assertEqual(notes[0].basename, "CoreIdea")

    def test_dense_circular_graph_linking(self):
        """Circular links (A -> B -> C -> A) must not cause recursion errors in graph resolution or audits."""
        (self.vault / "A.md").write_text("Links to [[B]]", encoding="utf-8")
        (self.vault / "B.md").write_text("Links to [[C]]", encoding="utf-8")
        (self.vault / "C.md").write_text("Links to [[A]]", encoding="utf-8")

        notes = discover_notes(self.vault, self.config)
        graph = VaultGraph(notes)
        self.assertTrue(graph.connected("A", "B"))
        self.assertTrue(graph.connected("B", "C"))
        self.assertTrue(graph.connected("C", "A"))

        # Verify no orphan findings
        self.config.audits.orphan_notes = True
        findings = graph.findings(self.config)
        orphans = [f for f in findings if f.kind == "orphan_note"]
        self.assertEqual(len(orphans), 0)

    def test_huge_note_chunking_and_embedding(self):
        """A huge note with 10,000 words chunks properly without memory or boundary exceptions."""
        words = ["fruit", "health", "nutrition", "apple", "banana"] * 2000  # 10,000 words
        huge_text = " ".join(words)
        (self.vault / "Huge.md").write_text(huge_text, encoding="utf-8")
        (self.vault / "Short.md").write_text("Fruit nutrition benefits.", encoding="utf-8")

        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            self.assertEqual(len(state["notes"]), 2)
            self.assertEqual(state["index_stats"].embedded, 2)
