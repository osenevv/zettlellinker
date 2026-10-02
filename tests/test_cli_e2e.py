from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CLIEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "NoteA.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        (self.vault / "NoteB.md").write_text("Fruit health benefits.", encoding="utf-8")
        self.python = sys.executable

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        import os
        env = dict(os.environ, ZETTELLINKER_TEST_EMBEDDER="1")
        cmd = [self.python, "-m", "zettellinker.cli", *args]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=self.vault, env=env)

    def test_version_flag(self):
        res = self.run_cli("--version")
        self.assertEqual(res.returncode, 0)
        self.assertIn("0.2.0", res.stdout)

    def test_init_creates_config_file(self):
        config_file = self.vault / ".zettellinker.yml"
        self.assertFalse(config_file.exists())
        res = self.run_cli("init", "--vault", str(self.vault))
        self.assertEqual(res.returncode, 0)
        self.assertTrue(config_file.exists())
        self.assertIn("Configuration ready", res.stdout)

    def test_settings_view_and_update(self):
        self.run_cli("init", "--vault", str(self.vault))
        # View settings
        res = self.run_cli("settings", "--vault", str(self.vault))
        self.assertEqual(res.returncode, 0)
        self.assertIn("threshold: 0.6", res.stdout)

        # Update settings
        res_update = self.run_cli(
            "settings",
            "--vault",
            str(self.vault),
            "--similarity-threshold",
            "0.78",
            "--auto-write",
            "on",
        )
        self.assertEqual(res_update.returncode, 0)
        self.assertIn("Settings saved", res_update.stdout)

        # Verify persistence
        res_check = self.run_cli("settings", "--vault", str(self.vault))
        self.assertIn("threshold: 0.78", res_check.stdout)
        self.assertIn("enabled: true", res_check.stdout)

    def test_doctor_command_json_format(self):
        res = self.run_cli("doctor", "--vault", str(self.vault), "--format", "json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertTrue(data["ok"])
        checks = data["checks"]
        self.assertTrue(checks["python"]["ok"])
        self.assertTrue(checks["vault_readable"]["ok"])
        self.assertTrue(checks["sentence_transformers"]["ok"])
        self.assertTrue(checks["langchain"]["ok"])
        self.assertTrue(checks["langchain_core"]["ok"])
        self.assertTrue(checks["langgraph"]["ok"])

    def test_nonexistent_vault_exits_with_error_code(self):
        bad_path = Path(self.temp.name) / "does_not_exist"
        res = self.run_cli("scan", "--vault", str(bad_path))
        self.assertEqual(res.returncode, 2)
        self.assertIn("does not exist", res.stderr)

    def test_suggest_unknown_note_exits_with_error(self):
        self.run_cli("init", "--vault", str(self.vault))
        res = self.run_cli("suggest", "GhostFile", "--vault", str(self.vault), "--yes")
        self.assertEqual(res.returncode, 2)
        self.assertIn("was not found", res.stderr)

    def test_suggest_ambiguous_note_reports_all_candidates(self):
        (self.vault / "Folder1").mkdir()
        (self.vault / "Folder2").mkdir()
        (self.vault / "Folder1" / "Duplicate.md").write_text("One", encoding="utf-8")
        (self.vault / "Folder2" / "Duplicate.md").write_text("Two", encoding="utf-8")

        res = self.run_cli("suggest", "Duplicate", "--vault", str(self.vault), "--yes")
        self.assertEqual(res.returncode, 2)
        self.assertIn("ambiguous", res.stderr)
        self.assertIn("Folder1/Duplicate.md", res.stderr)
        self.assertIn("Folder2/Duplicate.md", res.stderr)

    def test_undo_command_when_nothing_to_undo(self):
        res = self.run_cli("undo", "--vault", str(self.vault), "--format", "json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["command"], "undo")
        self.assertIsNone(data["transaction"])
        self.assertEqual(data["restored"], [])

    def test_scan_command_json_format(self):
        res = self.run_cli("scan", "--vault", str(self.vault), "--format", "json")
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["command"], "scan")
        self.assertIn("index", data)
        self.assertIn("suggestions", data)
        self.assertIn("findings", data)
        self.assertIsInstance(data["suggestions"], list)

    def test_scan_and_auto_write_flow(self):
        self.run_cli("settings", "--vault", str(self.vault), "--auto-write", "on")
        res_scan = self.run_cli("scan", "--vault", str(self.vault), "--format", "json")
        self.assertEqual(res_scan.returncode, 0)
        data = json.loads(res_scan.stdout)
        self.assertIsNotNone(data["write"])
        self.assertGreater(data["write"]["links_added"], 0)

        # Now test undo via CLI
        res_undo = self.run_cli("undo", "--vault", str(self.vault), "--format", "json")
        self.assertEqual(res_undo.returncode, 0)
        undo_data = json.loads(res_undo.stdout)
        self.assertIsNotNone(undo_data["transaction"])
        self.assertGreater(len(undo_data["restored"]), 0)
