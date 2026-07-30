from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from zettellinker.cli import main
from zettellinker.config import CONFIG_NAME, load_config


class ConfigCliTests(unittest.TestCase):
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
