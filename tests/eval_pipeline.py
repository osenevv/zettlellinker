from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from zettellinker.config import VaultConfig
from zettellinker.semantic import ExactIndex
from zettellinker.workflow import run_vault_audit, run_vault_scan
from zettellinker.writer import undo_last

from test_semantic import FakeEmbedder


class PipelineEvalSuite(unittest.TestCase):
    """Quality, accuracy, latency, and idempotency evaluation benchmark for LangGraph pipeline."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.cache = Path(self.temp.name) / "cache"
        self.vault.mkdir()
        self.config = VaultConfig()
        self.config.semantic.threshold = 0.5
        self.config.auto_write.anchor_threshold = 0.6
        self.config.audits.ghost_links = True
        self.config.audits.ambiguous_links = True
        self.config.audits.orphan_notes = True

        # Build realistic topic clusters
        # Cluster 1: Nutrition / Health
        (self.vault / "Nutrition.md").write_text(
            "# Nutrition\nFruit nutrition and healthy diet support human longevity.",
            encoding="utf-8",
        )
        (self.vault / "Diet.md").write_text(
            "# Diet\nDaily fruit consumption and balanced nutrition promote health.",
            encoding="utf-8",
        )
        # Cluster 2: Quantum Physics
        (self.vault / "Quantum_Mechanics.md").write_text(
            "# Quantum Mechanics\nQuantum physics principles and wave-particle duality research.",
            encoding="utf-8",
        )
        (self.vault / "Physics_Lab.md").write_text(
            "# Physics Lab\nExperimental quantum physics investigations and particle accelerators.",
            encoding="utf-8",
        )
        # Cluster 3: Graph Anomalies
        (self.vault / "sub1").mkdir()
        (self.vault / "sub2").mkdir()
        (self.vault / "sub1" / "Target.md").write_text("Sub1 target note.", encoding="utf-8")
        (self.vault / "sub2" / "Target.md").write_text("Sub2 target note.", encoding="utf-8")
        # Note with ghost link and ambiguous link
        (self.vault / "Anomalies.md").write_text(
            "# Anomalies\nLink to [[Target]] and link to missing [[NonExistentNote]].",
            encoding="utf-8",
        )
        # Orphan note
        (self.vault / "Orphan.md").write_text(
            "# Orphan\nCompletely isolated software notes without any references.",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_eval_retrieval_accuracy_mrr_and_recall(self):
        """Evaluate semantic link suggestion MRR (Mean Reciprocal Rank) and Recall@K."""
        ground_truth_pairs = {
            "Nutrition": "Diet",
            "Diet": "Nutrition",
            "Quantum_Mechanics": "Physics_Lab",
            "Physics_Lab": "Quantum_Mechanics",
        }

        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )

        suggestions = state.get("suggestions", [])
        by_source: dict[str, list[str]] = {}
        for s in suggestions:
            by_source.setdefault(s.source, []).append(s.target)

        reciprocal_ranks: list[float] = []
        hits_at_1 = 0
        hits_at_3 = 0

        for source, expected_target in ground_truth_pairs.items():
            ranked_targets = by_source.get(source, [])
            if expected_target in ranked_targets:
                rank = ranked_targets.index(expected_target) + 1
                reciprocal_ranks.append(1.0 / rank)
                if rank == 1:
                    hits_at_1 += 1
                if rank <= 3:
                    hits_at_3 += 1
            else:
                reciprocal_ranks.append(0.0)

        mrr = sum(reciprocal_ranks) / len(ground_truth_pairs)
        recall_at_1 = hits_at_1 / len(ground_truth_pairs)
        recall_at_3 = hits_at_3 / len(ground_truth_pairs)

        print(f"\n[EVAL METRICS] MRR: {mrr:.4f}, Recall@1: {recall_at_1:.2f}, Recall@3: {recall_at_3:.2f}")

        # Quality thresholds:
        self.assertGreaterEqual(mrr, 0.85, f"MRR {mrr} fell below threshold 0.85")
        self.assertGreaterEqual(recall_at_1, 0.75, f"Recall@1 {recall_at_1} fell below threshold 0.75")
        self.assertEqual(recall_at_3, 1.0, f"Recall@3 {recall_at_3} did not achieve 100%")

    def test_eval_graph_audit_precision_and_recall(self):
        """Evaluate precision and recall of graph health audit anomaly detection."""
        state = run_vault_audit(self.vault, self.config)
        findings = state.get("audit_findings", [])

        ghost_targets = {f.target for f in findings if f.kind == "ghost_link"}
        ambiguous_targets = {f.target for f in findings if f.kind == "ambiguous_link"}
        orphan_sources = {f.source for f in findings if f.kind == "orphan_note"}

        # Exact expected anomalies
        self.assertEqual(ghost_targets, {"NonExistentNote"}, "Ghost link detection mismatch")
        self.assertEqual(ambiguous_targets, {"Target"}, "Ambiguous link detection mismatch")
        self.assertIn("Orphan", orphan_sources, "Orphan note not detected")

    def test_eval_pipeline_latency_budget(self):
        """Evaluate end-to-end pipeline execution time stays within latency budget."""
        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            start = time.perf_counter()
            state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            duration = time.perf_counter() - start

        print(f"[EVAL METRICS] Pipeline Latency: {duration * 1000:.2f} ms")
        self.assertLess(duration, 1.0, f"Pipeline took {duration:.3f}s, exceeding 1.0s budget")
        self.assertGreater(len(state.get("notes", [])), 0)

    def test_eval_autowrite_idempotency_and_undo_safety(self):
        """Evaluate that running auto-write repeatedly is strictly idempotent, and undo safely reverts all changes."""
        self.config.auto_write.enabled = True
        before_state = {p: p.read_text(encoding="utf-8") for p in self.vault.rglob("*.md")}

        with patch("zettellinker.semantic.user_cache_path", return_value=self.cache):
            # First pass: writes links
            first_state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            first_write = first_state.get("write_result")
            self.assertIsNotNone(first_write)
            self.assertGreater(first_write.links_added, 0)

            # Second pass: already connected notes should result in 0 new links added
            second_state = run_vault_scan(
                self.vault,
                self.config,
                embedder=FakeEmbedder(),
                index_factory=ExactIndex,
            )
            second_write = second_state.get("write_result")
            self.assertTrue(
                second_write is None or second_write.links_added == 0,
                f"Second pass added duplicate links: {getattr(second_write, 'links_added', 0)}",
            )

            # Undo reverts everything back to original state
            undo_result = undo_last(self.vault)
            self.assertGreater(len(undo_result.modified), 0)
            for path, original_text in before_state.items():
                self.assertEqual(path.read_text(encoding="utf-8"), original_text)


if __name__ == "__main__":
    unittest.main()
