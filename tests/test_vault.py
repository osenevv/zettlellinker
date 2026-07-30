from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zettellinker.config import VaultConfig
from zettellinker.vault import VaultGraph, clean_for_embedding, discover_notes, parse_wikilinks


class ParserTests(unittest.TestCase):
    def test_wikilinks_alias_fragment_embed_and_protected_code(self):
        text = """---
links: '[[Frontmatter]]'
---
[[Target_Note|Visible]] [[Folder/Other#Heading]] ![[Embedded]]
`[[InlineCode]]`
```md
[[FencedCode]]
```
"""
        links = parse_wikilinks(text)
        self.assertEqual([link.target for link in links], ["Target_Note", "Folder/Other", "Embedded"])
        self.assertEqual(links[0].alias, "Visible")
        self.assertEqual(links[1].fragment, "Heading")
        self.assertTrue(links[2].embed)

    def test_cleaning_uses_visible_text_and_configured_sections(self):
        text = """---
tag: private
---
# Topic
Body [[Target_Note|friendly words]].
## Sources
Ignored source.
## Result
Kept result.
"""
        cleaned = clean_for_embedding(text, ["Sources"])
        self.assertNotIn("private", cleaned)
        self.assertNotIn("Ignored", cleaned)
        self.assertIn("friendly words", cleaned)
        self.assertIn("Kept result", cleaned)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, text: str = "") -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_recursive_discovery_hidden_ignore_and_ambiguity(self):
        self.write("A.md", "[[Ideas]] [[work/Ideas]] [[missing]]")
        self.write("work/Ideas.md", "Work")
        self.write("personal/Ideas.md", "Personal")
        self.write(".obsidian/Internal.md", "Ignored")
        notes = discover_notes(self.vault, VaultConfig())
        self.assertEqual([note.identity for note in notes], ["A", "personal/Ideas", "work/Ideas"])
        graph = VaultGraph(notes)
        statuses = [(item.target_text, item.status) for item in graph.resolutions]
        self.assertIn(("Ideas", "ambiguous"), statuses)
        self.assertIn(("work/Ideas", "resolved"), statuses)
        self.assertIn(("missing", "unresolved"), statuses)

    def test_case_mismatch_is_unresolved_on_every_platform(self):
        self.write("Machine_Learning.md", "Body")
        self.write("Index.md", "[[machine_learning]]")
        graph = VaultGraph(discover_notes(self.vault, VaultConfig()))
        resolution = graph.resolutions[0]
        self.assertEqual(resolution.status, "unresolved")
        self.assertEqual(resolution.matches, ("Machine_Learning",))

    def test_include_and_exclude_globs(self):
        self.write("Root.md", "Root")
        self.write("notes/Keep.md", "Keep")
        self.write("notes/Skip.md", "Skip")
        config = VaultConfig(include_globs=["notes/*.md"], exclude_globs=["**/Skip.md"])
        notes = discover_notes(self.vault, config)
        self.assertEqual([note.identity for note in notes], ["notes/Keep"])

    def test_exclude_name_contains_is_case_insensitive(self):
        (self.vault / "Useful Source Note.md").write_text("source", encoding="utf-8")
        (self.vault / "Useful Note.md").write_text("keep", encoding="utf-8")
        config = VaultConfig(exclude_name_contains=["SOURCE"])
        identities = {note.identity for note in discover_notes(self.vault, config)}
        self.assertNotIn("Useful Source Note", identities)
        self.assertIn("Useful Note", identities)

    def test_target_paths_limit_discovery_and_include_subfolders(self):
        (self.vault / "SecondBrain").mkdir()
        (self.vault / "SecondBrain" / "Nested").mkdir()
        (self.vault / "SecondBrain" / "One.md").write_text("one", encoding="utf-8")
        (self.vault / "SecondBrain" / "Nested" / "Two.md").write_text("two", encoding="utf-8")
        (self.vault / "Outside.md").write_text("outside", encoding="utf-8")
        config = VaultConfig(target_paths=["SecondBrain"])
        identities = {note.identity for note in discover_notes(self.vault, config)}
        self.assertIn("SecondBrain/One", identities)
        self.assertIn("SecondBrain/Nested/Two", identities)
        self.assertNotIn("Outside", identities)

    def test_embedded_markdown_is_connection_but_attachment_is_ignored(self):
        self.write("A.md", "![[B]] ![[image.png]]")
        self.write("B.md", "Body")
        graph = VaultGraph(discover_notes(self.vault, VaultConfig()))
        self.assertIn("B", graph.outgoing["A"])
        self.assertEqual(len(graph.resolutions), 1)

    def test_markdown_note_names_with_dots_are_not_attachments(self):
        self.write("A.md", "[[bookkeeper.SKILL]] [[Diary/2022-06-11 20.30]]")
        self.write("bookkeeper.SKILL.md", "Skill note")
        self.write("Diary/2022-06-11 20.30.md", "Daily note")

        graph = VaultGraph(discover_notes(self.vault, VaultConfig()))

        self.assertEqual(
            [(item.target_text, item.status) for item in graph.resolutions],
            [("bookkeeper.SKILL", "resolved"), ("Diary/2022-06-11 20.30", "resolved")],
        )
