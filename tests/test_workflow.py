from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zettellinker.config import VaultConfig
from zettellinker.semantic import ExactIndex
from zettellinker.workflow import (
    create_audit_graph,
    create_scan_graph,
    create_suggest_graph,
    run_note_suggest,
    run_vault_audit,
    run_vault_scan,
)

from test_semantic import FakeEmbedder


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        (self.vault / "Apple.md").write_text("Fruit nutrition supports long term health.", encoding="utf-8")
        (self.vault / "Banana.md").write_text("Fruit nutrition and health benefits. [[GhostNote]]", encoding="utf-8")
        (self.vault / "Quantum.md").write_text("Quantum physics research.", encoding="utf-8")
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.5
        self.config.auto_write.anchor_threshold = 0.6

    def tearDown(self):
        self.temp.cleanup()

    def test_create_graphs_compile(self):
        self.assertIsNotNone(create_scan_graph())
        self.assertIsNotNone(create_suggest_graph())
        self.assertIsNotNone(create_audit_graph())

    def test_run_vault_scan_graph(self):
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            self.assertEqual(len(state["notes"]), 3)
            self.assertEqual(len(state["documents"]), 3)
            self.assertIsNotNone(state["graph"])
            self.assertIsNotNone(state["vectorstore"])
            self.assertIsNotNone(state["index_stats"])
            self.assertEqual(state["index_stats"].embedded, 3)
            self.assertGreaterEqual(len(state["suggestions"]), 1)
            # Verify Banana -> GhostNote was caught by audit node
            ghost_findings = [f for f in state["audit_findings"] if f.kind == "ghost_link"]
            self.assertEqual(len(ghost_findings), 1)
            self.assertEqual(ghost_findings[0].target, "GhostNote")
            # Auto-write disabled, so write_result is None
            self.assertIsNone(state.get("write_result"))
            # Logs accumulated
            self.assertGreater(len(state["logs"]), 3)

    def test_run_vault_scan_with_autowrite_conditional_edge(self):
        self.config.auto_write.enabled = True
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            self.assertIsNotNone(state.get("write_result"))
            write_res = state["write_result"]
            self.assertGreater(write_res.links_added, 0)
            self.assertGreater(len(write_res.modified), 0)
            modified_contents = [
                (self.vault / f"{name}.md").read_text(encoding="utf-8")
                for name in write_res.modified
            ]
            self.assertTrue(any("[[" in c for c in modified_contents))

    def test_run_note_suggest_graph(self):
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_note_suggest(
                self.vault,
                self.config,
                note="Apple",
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            self.assertEqual(state["target_note"], "Apple")
            suggestions = state["suggestions"]
            self.assertGreaterEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0].source, "Apple")
            self.assertEqual(suggestions[0].target, "Banana")

    def test_run_vault_audit_graph(self):
        state = run_vault_audit(self.vault, self.config)
        self.assertEqual(len(state["notes"]), 3)
        self.assertIsNotNone(state["graph"])
        findings = state["audit_findings"]
        ghosts = [f for f in findings if f.kind == "ghost_link"]
        self.assertEqual(len(ghosts), 1)
        self.assertEqual(ghosts[0].source, "Banana")
        self.assertEqual(ghosts[0].target, "GhostNote")
        # Audit graph does NOT run semantic indexing
        self.assertIsNone(state.get("index_stats"))
        self.assertIsNone(state.get("engine"))
