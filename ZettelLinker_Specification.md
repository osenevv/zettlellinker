# ZettelLinker: MVP Product and Technical Specification

## 1. Product Positioning

ZettelLinker is a fast, accessible semantic-linking and graph-health engine for
Obsidian vaults. The MVP uses full note bodies to find useful conceptual
connections without relying on title matching.

**Primary promise:** Find and optionally insert meaningful links across an Obsidian
vault quickly, locally, and with minimal user effort.

### Competitive focus

The primary differentiators are:

1. **Performance:** Incremental full-note embeddings and HNSW retrieval should
   provide fast repeated use as a vault grows.
2. **Accessibility:** Free local processing, modest hardware requirements,
   cross-platform operation, straightforward installation, terminal use, and
   stable JSON integration.

The MVP does not compete on visualization or automatic link-writing breadth.
Semantic Auto-Linker and other established plugins must be treated as performance
and usability benchmarks rather than ignored because of current adoption.

### Validated user pains

Community research indicates four recurring problems:

1. Finding relevant older notes becomes harder as a vault grows.
2. Remembering and manually creating useful links creates cognitive overhead.
3. Users want semantic discovery without sending private notes to an API.
4. Fixed auditing rules create noise because every vault uses different conventions.

The MVP therefore combines semantic rediscovery with configurable graph auditing
for Obsidian users. Support for other Markdown editors is outside the MVP scope.

## 2. Product Principles

1. **Local-first:** Note contents and embeddings stay on the user's machine by
   default. The embedding model may be downloaded on first use.
2. **Obsidian-focused:** Operate directly on Obsidian's Markdown vault and wikilink
   conventions without requiring a desktop plugin.
3. **Zero-config start:** Useful defaults must work immediately on an unfamiliar
   vault.
4. **Vault-specific behavior:** Naming, excluded sections, link policies, and other
   conventions must be configurable rather than imposed globally.
5. **Scalable search:** Use HNSW rather than exhaustive all-pairs comparison.
6. **Safe operation:** The MVP reports findings by default. Automatic writes are
   available only through explicit persistent vault configuration.

## 3. MVP Scope

### Included

- Cross-platform support for Windows, macOS, and Linux on Python 3.11 or newer.
- Recursive Markdown discovery, including nested subfolders.
- Standard wikilink parsing.
- Path-aware and basename-aware link resolution.
- Local embeddings with incremental caching.
- HNSW semantic-neighbor search.
- Optional automatic link insertion with configurable templates, confidence gates,
  atomic writes, an audit log, and undo support.
- Up to a configurable number of suggestions per note.
- Configurable graph-integrity audits.
- A vault-level configuration file produced by `zettellinker init`.
- Human-readable terminal reports and machine-readable JSON output containing the
  same findings.
- PyPI distribution with a single `pipx install zettellinker` application-install
  command after Python is available.

### Deferred until after the MVP

- Automatic reciprocal-link insertion beyond the configured semantic write policy.
- An Obsidian sidebar plugin. The first version may be a desktop-only TypeScript
  frontend that invokes the installed Python CLI and consumes its stable JSON
  output. Mobile support would require a separate TypeScript/WASM inference path
  or another backend and is not implied by the CLI architecture.
- Any other desktop interface.
- AI-assisted vault convention detection. This may run automatically when the user
  opts in, supplies an API key, and selects a sufficiently capable non-small model.
- On-demand AI-powered Idea Compass analysis for North, West, East, and South
  relationships. Its provider, model, prompt, confidence policy, and interface are
  intentionally unspecified until after the MVP is validated.
- Exhaustive semantic similarity guarantees.

## 4. CLI

The Python 3.11+ package exposes a `zettellinker` command. Python is the selected
MVP implementation language, and the terminal is the only MVP interface.

```text
zettellinker init --vault <path>
zettellinker suggest <note> --vault <path> [options]
zettellinker scan --vault <path> [options]
zettellinker undo --vault <path>
```

### `init`

`zettellinker init` recursively inspects the vault and creates an editable
`.zettellinker.yml` with safe defaults and deterministic observations. It must not
send vault content to an external service in the MVP.

### `scan`

`zettellinker scan` performs whole-vault semantic discovery and enabled integrity
audits.

### `suggest`

`zettellinker suggest <note>` returns semantically related, currently unlinked
notes for one vault-relative note. It uses the same configuration and text/JSON
formats as `scan`.

### Common options

Common inputs:

- `--vault <path>`: Absolute or relative vault path; defaults to the current
  directory so commands work naturally from an Obsidian terminal.
- `--threshold <float>`: Minimum semantic similarity from `0.0` to `1.0`;
  default `0.60`.
- `--limit <int>`: Maximum semantic suggestions **per note**; default `5`.
- `--config <path>`: Optional alternate configuration path.
- `--reciprocal-mode <off|audit>`: Override the configured reciprocal policy.
- `--format <text|json>`: Select human-readable terminal output or stable,
  machine-readable JSON; default `text`.
- `--yes`: Accept safe non-destructive prompts, including the first model download,
  for scripts and automated environments.

Future versions may add `fix` to reciprocal mode, with preview and confirmation
before any write.

## 5. Vault Discovery and Identity

1. Recursively scan all `.md` files beneath the vault root.
2. Ignore hidden directories such as `.git`, `.obsidian`, and `.trash` by default.
   Users may explicitly include selected hidden directories in configuration.
3. Ignore filesystem symbolic links. The MVP must not traverse linked files or
   directories outside the physical vault tree.
4. Store note identities as normalized vault-relative paths without the `.md`
   suffix.
5. Support path-based wikilinks such as `[[work/Ideas]]`.
6. Resolve a basename-only link such as `[[Ideas]]` when exactly one matching note
   exists anywhere in the vault.
7. Report a basename-only link as ambiguous when multiple matching notes exist.
8. Resolve link paths with exact capitalization on every operating system. A
   case-mismatched target is reported as a broken link even on a case-insensitive
   filesystem.
9. Generate the shortest unambiguous target: use `[[Ideas]]` when the basename is
   unique, otherwise use a vault-relative target such as `[[work/Ideas]]`.
10. Never hardcode a filename capitalization convention.

Additional ignore-rule syntax remains to be finalized.

## 6. Markdown and Wikilink Parsing

Recognize at minimum:

- `[[Target]]`
- `[[Target|Alias]]`
- `[[Target#Section]]`
- `[[folder/Target]]`
- `![[Target]]` and its alias, heading, and path variants when the target is a
  Markdown note. Embedded notes count as existing graph connections and must not
  be suggested again.

Only Obsidian-style wikilinks define graph edges in the MVP. Standard Markdown
links such as `[label](Target.md)` are not parsed as note connections.

Normalize a target by removing its alias and heading or block fragment while
preserving its path. The MVP resolves only the main note and does not validate
whether an internal heading or block exists. Link resolution must distinguish:

- resolved links;
- unresolved or ghost links;
- ambiguous basename links.

The MVP resolves and audits Markdown note targets only. Embedded or linked images,
PDFs, audio, canvases, and other attachments are outside scope and must not be
reported as ghost notes.

Regex may be used for the MVP only if tests cover escaping, aliases, fragments,
embeds, code blocks, and malformed links. Otherwise use a small purpose-built
parser.

## 7. Content Preparation

The default embedding input should represent the note's human-readable meaning,
not its graph syntax.

Default behavior:

- Exclude YAML frontmatter at the top of a note.
- Remove wikilink markup while retaining useful visible text.
- Embed all ordinary sections.

The configuration may exclude headings, folders, frontmatter fields, tags, code
blocks, or other vault-specific content. `## Connections` and `## Source` are not
special unless configured by the user.

## 8. Embeddings and Cache

- Use `sentence-transformers/all-MiniLM-L6-v2` as the free, local MVP default. It
  is Apache-2.0 licensed, English-focused, and produces 384-dimensional vectors.
- Treat each vault as single-language. The MVP officially supports English only.
  Other single-language vaults may work when the user selects a compatible model,
  but their result quality is not guaranteed or tested for the MVP.
- Permit downloading the model on first use.
- Do not require an API key.
- Keep note contents, vectors, and the search index local by default.
- Cache by normalized relative path, content hash, model identifier, and content
  preparation settings.
- Re-embed only new or changed notes.
- Remove deleted notes from the cache and semantic index.
- Rebuild incompatible cache entries when the model or relevant configuration
  changes.
- Split notes longer than the model input limit into bounded chunks and aggregate
  their embeddings so later content is not silently discarded. The precise
  chunking and aggregation strategy must be covered by quality tests.

The cache and index must never be stored in the vault by default. They live in the
operating system's standard per-user application-data directory, selected through
a cross-platform path library. Each vault receives a stable cache identity. The
location remains configurable, and deleting it only causes a safe index rebuild.

## 9. HNSW Semantic Search

The MVP uses full-note MiniLM embeddings and HNSW to retrieve conceptually similar
unlinked notes. It does not label results as North, West, East, or South.

For each indexed note:

1. Query the HNSW index for candidate neighbors.
2. Exclude the note itself and notes already connected by a resolved wikilink.
3. Filter candidates below `--threshold`.
4. Return at most `--limit` candidates, sorted by descending cosine similarity.

`--limit` is a maximum, not a quota. Never pad results with weak relationships: if
only two candidates qualify, return two; if none qualify, return an empty result.

The persisted index must load efficiently between commands. Before a `suggest`
request, detect vault changes using inexpensive metadata and incrementally
re-embed every added or modified note, removing deleted notes. Suggestions must
wait for this refresh so results are current. Show progress when the refresh is not
effectively instantaneous, and preserve the previous valid index if interrupted.
The command must not re-embed unchanged notes.

Suggestions contain the target note and cosine similarity score. They make no
claim about opposition, causality, hierarchy, or direction.

Because HNSW is approximate, the product must not promise every pair above the
threshold. Tests must measure recall against exact search on smaller fixtures.
Index parameters must be configurable for users who prefer recall, speed, or lower
memory usage.

The implementation must process notes incrementally and avoid loading complete
vault contents into memory. "Unlimited scale" is not a meaningful guarantee on
finite hardware; benchmark targets will be defined separately.

## 10. Optional Automatic Linking

Report-only behavior is the default. Users may opt into automatic writing only
through the vault configuration; no command-line flag may bypass that setting.
When enabled, `scan` may add qualifying links throughout the vault, while
`suggest <note>` may modify only the selected source note. Both commands must state
their write scope before processing begins.
No Compass directions are written into note bodies, headings, YAML frontmatter, or
link labels. Optional writes add ordinary contextual Obsidian wikilinks by
preserving an existing phrase and wrapping it as
`[[Target Note|existing phrase]]`. The writer must not rewrite the surrounding
prose. When no safe phrase exists, add a plain `[[Target Note]]` to the vault's
configured Connections section. Compass directions remain absent from that
section. The section heading is configurable per vault and defaults to
`## Connections`; create it when absent and automatic writing requires it. Whether
reciprocal links are written remains configurable.

For a whole-vault pair when reciprocal writing is disabled, score both possible
inline placements: the best safe phrase in note A against note B's full embedding,
and the best safe phrase in B against A's full embedding. Insert only the
higher-scoring contextual link. Anchor selection uses body embeddings rather than
filename or title similarity.

If neither side has a safe inline phrase, place a plain link in the configured
Connections section of the note with fewer existing outgoing links. Break ties by
a stable normalized-path ordering so the result is deterministic. The default
entry format is a bare wikilink on its own line, `[[Target Note]]`, without a list
marker or Compass label. The template remains configurable per vault.

Automatic writes must:

1. Apply only relationships above the configured similarity threshold.
2. Skip ambiguous or weak results.
3. Preserve user-authored text and use atomic file replacement.
4. Record an audit log sufficient for `zettellinker undo`.
5. Remain idempotent so repeated scans do not duplicate links.

## 11. Configurable Graph Audits

No vault convention is universally treated as an error.

### Built-in audit capabilities

- Unresolved or ghost wikilinks.
- Ambiguous basename links.
- Optional orphan-note detection.
- Optional reciprocal-link detection.
- Optional user-defined filename validation.

### Reciprocal policy

- `off` (default): One-way links are valid and produce no warning.
- `audit`: Report one-way links without changing files.
- `fix` (future): Preview proposed reciprocal links and request confirmation before
  writing them.

## 12. Configuration Model

`.zettellinker.yml` should support at minimum:

- included and excluded file globs;
- ignored folders;
- content exclusions;
- link-resolution behavior;
- reciprocal policy;
- orphan-note policy;
- optional filename rules;
- embedding model;
- semantic threshold, inline-link threshold, and per-note limit;
- case-insensitive filename-substring exclusions, in addition to path globs;
- optional vault-relative target folders; an empty list analyzes the whole vault;
- HNSW tuning parameters;
- cache location.

The generated file must contain usable defaults and concise comments. Invalid
configuration must fail with a precise error rather than silently changing
behavior.

## 13. MVP Safety and Privacy

- Scanning and semantic indexing are read-only with respect to user notes.
- Cache writes must be atomic so interruption does not corrupt an existing cache.
- Generated embeddings and indexes must not create Git or cloud-sync noise inside
  the vault.
- No telemetry or note content leaves the machine by default.
- Future external-model features require explicit configuration and clear disclosure
  of what data will be sent.

## 14. Installation and First-Run Experience

Installation must be usable by non-technical users even though Python is an MVP
prerequisite.

- Publish the package on PyPI for installation with
  `pipx install zettellinker`.
- Document short, copy-paste installation paths for Windows, macOS, and Linux,
  including how to install Python and `pipx` when missing.
- Do not require users to install embedding, HNSW, compiler, or model dependencies
  individually.
- Detect unsupported Python versions and missing executable-path configuration with
  actionable, platform-specific messages.
- Show download and first-index progress instead of appearing frozen.
- Before the first model download, show the model name, approximate download size,
  source, license, and destination, then require confirmation. If input is not
  interactive, fail with instructions to rerun with `--yes` rather than hanging.
- Work unchanged inside an Obsidian terminal whose current directory is the vault.
- Provide `zettellinker doctor` to check Python, model, cache, vault access, and
  native-library availability.

## 15. MVP Validation

Tests must cover:

1. Recursive discovery and configurable exclusions on Windows, macOS, and Linux,
   including platform-specific path separators and case behavior.
2. Wikilinks with aliases, heading fragments, paths, duplicate basenames, unresolved
   targets, code blocks, and malformed input.
3. Incremental cache invalidation for additions, edits, deletions, model changes,
   and configuration changes.
4. HNSW result ordering, threshold filtering, per-note limits, and measured recall
   against exact search fixtures.
5. Configurable audits whose defaults do not impose reciprocal or filename rules.
6. Repeatable performance benchmarks for initial indexing, incremental updates,
   single-note suggestions, whole-vault scans, peak memory, and index size.
7. Comparative benchmarks against relevant local Obsidian semantic-linking tools
   where equivalent workflows can be measured fairly.

### Performance reporting

Benchmarks must publish initial-index time, incremental-index time, warm suggestion
latency, whole-vault scan time, peak RAM, index size, and hardware configuration.

## 16. Open MVP Decisions

- HNSW library and persistence format.
- Whether additional report formats beyond terminal text and JSON are needed.
- Concrete performance benchmark and hardware profile.
- Exact behavior when a vault moves and its external cache identity changes.
