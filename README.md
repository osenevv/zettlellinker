# ZettelLinker

The [Zettelkasten method](https://en.wikipedia.org/wiki/Zettelkasten) depends on links. Each note gains value from what it connects to — but finding those connections manually gets harder as a vault grows. Notes that belong together stay apart.

ZettelLinker reads your vault, computes semantic similarity between notes using local AI embeddings, and writes `[[wikilinks]]` where they're missing. No internet. No API keys.

---

## What it does

- Finds related notes by meaning, not keywords
- Inserts `[[wikilinks]]` inline or under a heading in your files
- Detects ghost links, ambiguous links, and orphan notes
- Reverses every write with one click or `Cmd/Ctrl+Z`
- Exports results as CSV or Markdown

---

## Installation

### Binary (recommended)

Download from [Releases](https://github.com/osenevv/zettlellinker/releases):

| OS | File |
|---|---|
| macOS Apple Silicon | `zettellinker-macos-arm64` |
| macOS Intel | `zettellinker-macos-x64` |
| Windows 64-bit | `zettellinker-windows-x64.exe` |
| Linux 64-bit | `zettellinker-linux-x64` |

macOS/Linux: make it executable before running.
```bash
chmod +x zettellinker
```

### Python (pipx)

Requires Python 3.11+.

```bash
pipx install .
```

---

## Quick start

```bash
cd /path/to/your/obsidian/vault

zettellinker init              # create .zettellinker.yml
zettellinker scan              # scan vault, show suggestions
zettellinker suggest "Note.md" # suggestions for one note
zettellinker settings --auto-write on
zettellinker scan              # scan and write links
zettellinker undo              # revert if needed
```

Or open the GUI:

```bash
zettellinker gui
```

---

## Desktop GUI

`zettellinker gui` opens a native window.

| Button | Action |
|---|---|
| Scan Whole Vault | Finds link suggestions across all notes |
| Apply Connections | Writes links into your files |
| Undo | Reverts the last write (`Cmd+Z` / `Ctrl+Z` also works) |
| Export | Saves results as CSV or Markdown |
| Inspect Note | Shows suggestions for a specific note |

Hover over any label to see what that setting does.

---

## How links get written

Two strategies, in order of preference:

1. **Inline** — wraps a matching phrase as `[[Target|phrase]]` when the similarity score meets the Inline Threshold
2. **Under a heading** — appends `[[Target]]` under the Connections Heading. Creates that heading at the note's bottom if it doesn't exist.

---

## Settings

| Setting | Default | What it controls |
|---|---|---|
| Similarity Threshold | `0.60` | Minimum score to suggest a link (0–1). Higher = stricter. |
| Inline Threshold | `0.72` | Minimum score to insert a link inside sentence text. |
| Suggestions Limit | `5` | Max suggestions per note. |
| Connections Heading | `## Connections` | Heading that fallback links are appended under. |
| Exclude Substrings | *(empty)* | Skips notes whose name or folder contains these words. |
| Exclude Specific Files | *(empty)* | Skips individual files by relative path. |

Saved to `.zettellinker.yml` in your vault.

---

## Configuration file

```yaml
target_paths: []              # Limit scanning to these subfolders (empty = whole vault)
exclude_name_contains: []     # Skip notes whose name matches these fragments
exclude_files: []             # Skip specific files by relative path
semantic:
  model: sentence-transformers/all-MiniLM-L6-v2
  threshold: 0.60
  limit: 5
audits:
  ghost_links: true           # Links to notes that don't exist
  ambiguous_links: true       # Links that match more than one note
  orphan_notes: false         # Notes with no links in or out
auto_write:
  enabled: false
  connections_heading: "## Connections"
  anchor_threshold: 0.72
```

---

## CLI reference

| Command | Description |
|---|---|
| `zettellinker gui` | Open the desktop GUI |
| `zettellinker init` | Create vault config file |
| `zettellinker scan` | Scan vault and show suggestions |
| `zettellinker suggest <NOTE>` | Suggestions for one note |
| `zettellinker settings [OPTIONS]` | View or update settings |
| `zettellinker undo` | Revert last write |
| `zettellinker doctor` | Check installation |

---

## Privacy

All processing runs locally using `all-MiniLM-L6-v2`. Nothing leaves your machine.

---

## License

MIT
