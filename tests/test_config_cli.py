from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from zettellinker.cli import main
from zettellinker.config import CONFIG_NAME, get_dark_mode, load_config, save_dark_mode, save_last_vault_path


class ConfigCliTests(unittest.TestCase):
    def test_dark_mode_is_remembered_beside_the_vault_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "app.yml"
            with patch("zettellinker.config.GLOBAL_CONFIG_PATH", path):
                save_last_vault_path("/tmp")
                self.assertFalse(get_dark_mode())
                save_dark_mode(True)
                self.assertTrue(get_dark_mode())
                save_last_vault_path("/tmp")
                self.assertTrue(get_dark_mode())

    def test_default_semantic_threshold_is_point_six(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config(Path(temporary))
        self.assertEqual(config.semantic.threshold, 0.60)

    def test_init_detects_existing_connections_heading(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / "A.md").write_text("# A\n\n### Related Notes\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["init", "--vault", str(vault)])
            self.assertEqual(result, 0)
            self.assertTrue((vault / CONFIG_NAME).exists())
            config = load_config(vault)
            self.assertEqual(config.auto_write.connections_heading, "### Related Notes")
            self.assertFalse(config.auto_write.enabled)

    def test_index_must_be_exact_or_usearch(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / CONFIG_NAME).write_text("semantic:\n  index: hnsw\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "semantic.index"):
                load_config(vault)

    def test_audit_command_reports_ghost_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / "Banana.md").write_text("Fruit. [[GhostNote]]", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["audit", "--vault", str(vault)])
            self.assertEqual(result, 0)
            text = output.getvalue()
            self.assertIn("Findings: 1", text)
            self.assertIn("ghost_link: Banana -> GhostNote", text)

    def test_unknown_config_key_fails_precisely(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / CONFIG_NAME).write_text("unknown: true\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown configuration keys"):
                load_config(vault)

    def test_settings_command_persists_thresholds_and_name_exclusions(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            result = main([
                "settings", "--vault", str(vault),
                "--similarity-threshold", "0.65",
                "--inline-threshold", "0.70",
                "--target", "SecondBrain notes",
                "--exclude-name", "Source",
            ])
            self.assertEqual(result, 0)
            config = load_config(vault)
            self.assertEqual(config.semantic.threshold, 0.65)
            self.assertEqual(config.auto_write.anchor_threshold, 0.70)
            self.assertEqual(config.target_paths, ["SecondBrain notes"])
            self.assertEqual(config.exclude_name_contains, ["Source"])
