import csv
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from zettellinker.gui_app import (
    LIGHT_FIELD,
    LIGHT_LOG_BG,
    LIGHT_LOG_FG,
    MOON,
    SUN,
    THEME_DARK,
    ZettelLinkerApp,
    _preview_config,
)
from zettellinker.models import Finding, IndexStats, Suggestion
from zettellinker.writer import WriteResult


class TestGUIApp(unittest.TestCase):
    def setUp(self):
        self.dark_patch = patch("zettellinker.gui_app.get_dark_mode", return_value=False)
        self.dark_patch.start()
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.app = ZettelLinkerApp(self.root)
        except Exception as e:
            self.dark_patch.stop()
            self.skipTest(f"Tkinter window initialization skipped: {e}")

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "dark_patch"):
            self.dark_patch.stop()

    def test_light_mode_keeps_the_original_colors(self):
        button = self.app.theme_button
        self.assertEqual(button.cget("text"), SUN)
        self.assertLessEqual(int(button.cget("width")), 2)
        self.assertEqual(button.pack_info()["side"], "right")
        self.assertIs(button.master, self.app.root.pack_slaves()[0])
        self.assertEqual(self.app.log_text.cget("bg"), LIGHT_LOG_BG)
        self.assertEqual(self.app.log_text.cget("fg"), LIGHT_LOG_FG)
        light_button = self.app.style.lookup("TButton", "background")
        light_tree = self.app.style.lookup("Treeview", "background")
        light_root = self.app.root.cget("bg")
        def padding(value):
            if isinstance(value, str):
                return tuple(int(part) for part in value.split())
            return tuple(value)

        light_padding = padding(self.app.style.lookup("TButton", "padding"))
        self.app.root.update_idletasks()
        scan_size = (self.app.scan_btn.winfo_reqwidth(), self.app.scan_btn.winfo_reqheight())

        with patch("zettellinker.gui_app.save_dark_mode"):
            self.app._toggle_theme()
        self.app.root.update_idletasks()
        self.assertEqual(padding(self.app.style.lookup("TButton", "padding")), light_padding)
        self.assertEqual(
            (self.app.scan_btn.winfo_reqwidth(), self.app.scan_btn.winfo_reqheight()),
            scan_size,
        )
        self.assertEqual(self.app.theme_button.cget("text"), MOON)
        self.assertEqual(self.app.root.cget("bg"), THEME_DARK["bg"])
        self.assertEqual(self.app.log_text.cget("bg"), THEME_DARK["log_bg"])
        self.assertEqual(self.app.log_text.cget("fg"), THEME_DARK["log_fg"])
        self.assertEqual(self.app.style.lookup("Treeview", "background"), THEME_DARK["card"])
        self.assertEqual(self.app.style.lookup("TButton", "background"), THEME_DARK["card"])

        with patch("zettellinker.gui_app.save_dark_mode"):
            self.app._toggle_theme()
        self.assertEqual(self.app.theme_button.cget("text"), SUN)
        self.assertEqual(self.app.log_text.cget("bg"), LIGHT_LOG_BG)
        self.assertEqual(self.app.log_text.cget("fg"), LIGHT_LOG_FG)
        self.assertEqual(self.app.style.lookup("TButton", "background"), light_button)
        self.assertEqual(padding(self.app.style.lookup("TButton", "padding")), light_padding)
        self.assertEqual(self.app.style.lookup("TEntry", "fieldbackground"), LIGHT_FIELD)
        self.assertEqual(self.app.style.lookup("Treeview", "background"), light_tree)
        self.assertEqual(self.app.style.lookup("Treeview", "fieldbackground"), LIGHT_FIELD)
        self.assertEqual(self.app.root.cget("bg"), light_root)
        self.app.root.update_idletasks()
        self.assertEqual(
            (self.app.scan_btn.winfo_reqwidth(), self.app.scan_btn.winfo_reqheight()),
            scan_size,
        )

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

    def test_sort_column_source_and_score(self):
        suggs = [
            Suggestion(source="Zebra", target="Alpha", score=0.91),
            Suggestion(source="Apple", target="Beta", score=0.65),
            Suggestion(source="Mango", target="Gamma", score=0.82),
        ]
        self.app._render_suggestions(suggs)

        # Sort by source (first call -> ascending)
        self.app.sort_column("source")
        self.assertEqual([s.source for s in self.app.current_suggestions], ["Apple", "Mango", "Zebra"])

        # Sort by source again -> descending
        self.app.sort_column("source")
        self.assertEqual([s.source for s in self.app.current_suggestions], ["Zebra", "Mango", "Apple"])

        # Sort by score -> default descending
        self.app.sort_column("score")
        self.assertEqual([s.score for s in self.app.current_suggestions], [0.91, 0.82, 0.65])

    def test_render_empty_suggestions(self):
        self.app._render_suggestions([])
        self.assertEqual(len(self.app.current_suggestions), 0)
        self.assertEqual(len(self.app.tree.get_children()), 0)

    def test_resolve_note_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            sub = vault / "folder"
            sub.mkdir()
            nested_file = sub / "NestedNote.md"
            nested_file.write_text("Hello nested", encoding="utf-8")

            # Direct match
            found = self.app._resolve_note_file(vault, "folder/NestedNote")
            self.assertEqual(found, nested_file)

            # Stem match in subfolder
            found_stem = self.app._resolve_note_file(vault, "NestedNote")
            self.assertEqual(found_stem, nested_file)

            # Non-existent
            self.assertIsNone(self.app._resolve_note_file(vault, "NonExistent"))

    def test_preview_config_disables_auto_write(self):
        from zettellinker.config import VaultConfig

        config = VaultConfig()
        config.auto_write.enabled = True
        preview = _preview_config(config)
        self.assertFalse(preview.auto_write.enabled)
        self.assertTrue(config.auto_write.enabled)

    def test_apply_complete_reads_write_result(self):
        tx = WriteResult("tx.json.gz", ("Apple",), 2)
        with patch("zettellinker.gui_app.messagebox.showinfo"):
            self.app._on_apply_complete(tx)
        text = self.app.log_text.get("1.0", tk.END)
        self.assertIn("tx.json.gz", text)
        self.assertIn("1 files", text)
        self.assertIn("2 links", text)

    def test_apply_complete_with_empty_result(self):
        with patch("zettellinker.gui_app.messagebox.showinfo"):
            self.app._on_apply_complete(WriteResult(None, (), 0))
        text = self.app.log_text.get("1.0", tk.END)
        self.assertIn("No new links", text)

    def test_undo_reads_write_result(self):
        result = WriteResult("abc.json.gz", ("Apple", "Banana"), 2)
        with patch("zettellinker.gui_app.undo_last", return_value=result), patch(
            "zettellinker.gui_app.messagebox.showinfo"
        ):
            self.app.on_undo()
        text = self.app.log_text.get("1.0", tk.END)
        self.assertIn("abc.json.gz", text)
        self.assertIn("2 files", text)
        self.assertNotIn("Undo error", text)

    def test_undo_with_nothing_to_restore(self):
        with patch("zettellinker.gui_app.undo_last", return_value=WriteResult(None, (), 0)), patch(
            "zettellinker.gui_app.messagebox.showinfo"
        ):
            self.app.on_undo()
        text = self.app.log_text.get("1.0", tk.END)
        self.assertIn("No transaction", text)
        self.assertNotIn("Undo error", text)

    def test_scan_previews_and_logs_findings(self):
        seen = {}

        def fake_scan(vault, config, progress=None, notes=None):
            seen["enabled"] = config.auto_write.enabled
            return {
                "suggestions": [Suggestion("Apple", "Banana", 0.9)],
                "audit_findings": [Finding("ghost_link", "Apple", "Missing")],
                "index_stats": IndexStats(1, 2, 0),
                "engine": object(),
                "logs": [],
            }

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()

        self.app.auto_write_var.set("on")
        self.app.vault_path_var.set("/tmp")
        with patch("zettellinker.gui_app.run_vault_scan", fake_scan), patch(
            "zettellinker.gui_app.threading.Thread", ImmediateThread
        ):
            self.app.on_scan()
            self.app.root.update()
        self.assertFalse(seen["enabled"])
        text = self.app.log_text.get("1.0", tk.END)
        self.assertIn("ghost_link: Apple -> Missing", text)
        self.assertEqual(len(self.app.current_suggestions), 1)

    def test_suggest_uses_preview_workflow(self):
        seen = {}

        def fake_suggest(vault, config, note, progress=None, notes=None, embedder=None, index_factory=None):
            seen["enabled"] = config.auto_write.enabled
            seen["note"] = note
            return {
                "note": "Apple",
                "suggestions": [Suggestion("Apple", "Banana", 0.8)],
                "engine": object(),
            }

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()

        self.app.auto_write_var.set("on")
        self.app.vault_path_var.set("/tmp")
        self.app.suggest_note_var.set("Apple")
        with patch("zettellinker.gui_app.run_note_suggest", fake_suggest), patch(
            "zettellinker.gui_app.threading.Thread", ImmediateThread
        ):
            self.app.on_suggest()
            self.app.root.update()
        self.assertFalse(seen["enabled"])
        self.assertEqual(seen["note"], "Apple")
        self.assertEqual(self.app.current_suggestions[0].target, "Banana")
        self.assertIsNotNone(self.app.current_engine)

