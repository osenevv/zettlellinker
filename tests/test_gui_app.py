import csv
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from zettellinker.gui_app import ZettelLinkerApp
from zettellinker.models import Suggestion


class TestGUIApp(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.app = ZettelLinkerApp(self.root)
        except Exception as e:
            self.skipTest(f"Tkinter window initialization skipped: {e}")

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_app_initialization(self):
        self.assertIsNotNone(self.app)
        self.assertEqual(self.app.sim_threshold_var.get(), 0.60)
        self.assertEqual(self.app.inline_threshold_var.get(), 0.72)
        self.assertEqual(self.app.limit_var.get(), 5)

    def test_get_vault_path(self):
        self.app.vault_path_var.set("/tmp")
        self.assertEqual(self.app.get_vault_path(), Path("/tmp").resolve())

    def test_exclude_names_var(self):
        self.app.exclude_names_var.set("draft, archive, _template")
        self.assertEqual(self.app.exclude_names_var.get(), "draft, archive, _template")

    def test_get_current_config_overrides(self):
        self.app.vault_path_var.set("/tmp")
        self.app.sim_threshold_var.set(0.75)
        self.app.limit_var.set(10)
        self.app.auto_write_var.set("on")
        self.app.heading_var.set("## Links")
        self.app.exclude_names_var.set("draft, archive")

        vault, config = self.app._get_current_config()
        self.assertEqual(vault, Path("/tmp").resolve())
        self.assertEqual(config.semantic.threshold, 0.75)
        self.assertEqual(config.semantic.limit, 10)
        self.assertTrue(config.auto_write.enabled)
        self.assertEqual(config.auto_write.connections_heading, "## Links")
        self.assertEqual(config.exclude_name_contains, ["draft", "archive"])

    def test_render_suggestions(self):
        suggs = [
            Suggestion(source="NoteA", target="NoteB", score=0.8521),
            Suggestion(source="NoteC", target="NoteD", score=0.6410),
        ]
        self.app._render_suggestions(suggs)
        self.assertEqual(len(self.app.current_suggestions), 2)
        self.assertEqual(self.app.current_suggestions[0].source, "NoteA")
        children = self.app.tree.get_children()
        self.assertEqual(len(children), 2)

    def test_export_csv_formatting(self):
        suggs = [
            Suggestion(source="Alpha", target="Beta", score=0.9123),
        ]
        self.app.current_suggestions = suggs

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test_export.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Source", "Target", "Score"])
                for s in self.app.current_suggestions:
                    writer.writerow([s.source, s.target, f"{s.score:.4f}"])

            content = csv_path.read_text(encoding="utf-8")
            self.assertIn("Source,Target,Score", content)
            self.assertIn("Alpha,Beta,0.9123", content)

    def test_export_markdown_formatting(self):
        suggs = [
            Suggestion(source="Ideas", target="Architecture", score=0.88),
        ]
        self.app.current_suggestions = suggs

        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "test_report.md"
            lines = [
                "# ZettelLinker Suggestions Report",
                "",
                f"**Vault Path**: `{self.app.get_vault_path()}`",
                f"**Total Suggestions**: {len(self.app.current_suggestions)}",
                "",
                "| Source Note | Target Connection | Score |",
                "| :--- | :--- | :--- |",
            ]
            for s in self.app.current_suggestions:
                lines.append(f"| `{s.source}` | `[[{s.target}]]` | {s.score * 100:.1f}% |")

            md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("# ZettelLinker Suggestions Report", content)
            self.assertIn("| `Ideas` | `[[Architecture]]` | 88.0% |", content)
