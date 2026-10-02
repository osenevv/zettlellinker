from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
import yaml

from .config import CONFIG_NAME, VaultConfig, load_config, save_config, write_default_config
from .semantic import mark_model_downloaded, model_download_confirmed
from .vault import HEADING_RE, discover_notes
from .workflow import run_note_suggest, run_vault_audit, run_vault_scan
from .writer import undo_last


AUTO_WRITE_ON = "on"
AUTO_WRITE_OFF = "off"
FORMAT_TEXT = "text"
FORMAT_JSON = "json"

REQUIRED_MODULES = (
    "numpy",
    "yaml",
    "platformdirs",
    "sentence_transformers",
    "langchain",
    "langchain_core",
    "langgraph",
)
HEADING_KEYWORDS = ("connection", "related", "links")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zettellinker",
        description="Local semantic linking and graph health for Obsidian vaults.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create vault configuration")
    _vault_option(init)
    init.add_argument("--force", action="store_true", help="Replace existing configuration")

    suggest = commands.add_parser("suggest", help="Find semantic links for one note")
    suggest.add_argument("note", help="Vault-relative note path or unique basename")
    _analysis_options(suggest)

    scan = commands.add_parser("scan", help="Scan the whole vault")
    _analysis_options(scan)

    settings = commands.add_parser("settings", help="View or change vault settings")
    _vault_option(settings)
    settings.add_argument("--similarity-threshold", type=float, help="Minimum note similarity, 0.0 to 1.0")
    settings.add_argument("--inline-threshold", type=float, help="Minimum score for inline links, 0.0 to 1.0")
    settings.add_argument("--limit", type=int, help="Maximum suggestions per note")
    settings.add_argument("--target", action="append", default=[], metavar="PATH", help="Limit linking to a vault-relative folder (repeatable)")
    settings.add_argument("--remove-target", action="append", default=[], metavar="PATH", help="Remove a target folder (repeatable)")
    settings.add_argument("--clear-targets", action="store_true", help="Use the entire vault")
    settings.add_argument("--exclude-name", action="append", default=[], metavar="TEXT", help="Ignore files whose name contains TEXT (repeatable)")
    settings.add_argument("--remove-exclude-name", action="append", default=[], metavar="TEXT", help="Remove a filename exclusion (repeatable)")
    settings.add_argument("--clear-exclude-names", action="store_true", help="Remove every filename exclusion")
    settings.add_argument("--auto-write", choices=(AUTO_WRITE_ON, AUTO_WRITE_OFF), help="Enable or disable note editing")
    settings.add_argument("--connections-heading", help="Markdown heading used for appended links")

    undo = commands.add_parser("undo", help="Undo the most recent automatic write")
    _vault_option(undo)
    undo.add_argument("--format", choices=(FORMAT_TEXT, FORMAT_JSON), default=FORMAT_TEXT)

    doctor = commands.add_parser("doctor", help="Check the local installation")
    _vault_option(doctor)
    doctor.add_argument("--format", choices=(FORMAT_TEXT, FORMAT_JSON), default=FORMAT_TEXT)

    audit = commands.add_parser("audit", help="Report ghost links, ambiguous links, and orphans")
    _vault_option(audit)
    audit.add_argument("--format", choices=(FORMAT_TEXT, FORMAT_JSON), default=FORMAT_TEXT)

    gui = commands.add_parser("gui", help="Launch native desktop GUI app window")
    gui.add_argument("--web", action="store_true", help="Launch browser HTTP UI instead of native desktop app window")
    gui.add_argument("--port", type=int, default=8765, help="Port to run GUI server on (when --web is enabled)")
    gui.add_argument("--no-browser", action="store_true", help="Do not automatically open browser (when --web is enabled)")
    return parser


def _vault_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", type=Path, default=Path.cwd(), help="Vault root; default: current directory")


def _analysis_options(parser: argparse.ArgumentParser) -> None:
    _vault_option(parser)
    parser.add_argument("--config", type=Path, help="Alternate YAML configuration")
    parser.add_argument("--threshold", type=float, help="Override similarity threshold")
    parser.add_argument("--limit", type=int, help="Override maximum suggestions per note")
    parser.add_argument("--reciprocal-mode", choices=("off", "audit"), help="Override reciprocal audit mode")
    parser.add_argument("--format", choices=(FORMAT_TEXT, FORMAT_JSON), default=FORMAT_TEXT)
    parser.add_argument("--yes", action="store_true", help="Accept the first model download")


def _vault(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Vault directory does not exist: {resolved}")
    return resolved


def _confirm_model(model: str, assume_yes: bool) -> None:
    if os.environ.get("ZETTELLINKER_TEST_EMBEDDER") == "1" or model_download_confirmed(model):
        return
    message = (
        f"ZettelLinker needs to download {model} (approximately 91 MB) from Hugging Face.\n"
        "License: Apache-2.0. Storage: your operating system's user cache.\n"
        "Note contents will not be uploaded."
    )
    print(message, file=sys.stderr)
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise RuntimeError("Model download needs confirmation; rerun with --yes")
    answer = input("Download now? [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise RuntimeError("Model download cancelled")


def _apply_overrides(args: argparse.Namespace, config: VaultConfig) -> None:
    if getattr(args, "threshold", None) is not None:
        config.semantic.threshold = args.threshold
    if getattr(args, "limit", None) is not None:
        config.semantic.limit = args.limit
    if getattr(args, "reciprocal_mode", None) is not None:
        config.audits.reciprocal_mode = args.reciprocal_mode
    config.validate()


def _prepare(args: argparse.Namespace):
    vault = _vault(args.vault)
    config = load_config(vault, args.config)
    _apply_overrides(args, config)
    notes = discover_notes(vault, config)
    if any(note.clean_text for note in notes):
        _confirm_model(config.semantic.model, args.yes)
    return vault, config, notes


def _suggest(args: argparse.Namespace) -> int:
    vault, config, notes = _prepare(args)
    progress = lambda message: print(message, file=sys.stderr)
    if config.auto_write.enabled:
        print(f"Automatic writing enabled; write scope: {args.note}", file=sys.stderr)
    state = run_note_suggest(vault, config, args.note, progress=progress, notes=notes)
    mark_model_downloaded(config.semantic.model)
    suggestions = state.get("suggestions", [])
    stats = state.get("index_stats")
    stats_dict = stats.as_dict() if stats else {"embedded": 0, "reused": 0, "removed": 0}
    write_result = state.get("write_result")
    payload = {
        "command": "suggest",
        "note": state["note"],
        "index": stats_dict,
        "suggestions": [item.as_dict() for item in suggestions],
        "write": _write_payload(write_result),
    }
    _render(payload, args.format)
    return 0


def _scan(args: argparse.Namespace) -> int:
    vault, config, notes = _prepare(args)
    progress = lambda message: print(message, file=sys.stderr)
    if config.auto_write.enabled:
        print("Automatic writing enabled; write scope: entire vault", file=sys.stderr)
    state = run_vault_scan(vault, config, progress=progress, notes=notes)
    mark_model_downloaded(config.semantic.model)
    suggestions = state.get("suggestions", [])
    findings = state.get("audit_findings", [])
    stats = state.get("index_stats")
    stats_dict = stats.as_dict() if stats else {"embedded": 0, "reused": 0, "removed": 0}
    write_result = state.get("write_result")
    payload = {
        "command": "scan",
        "index": stats_dict,
        "suggestions": [item.as_dict() for item in suggestions],
        "findings": [item.as_dict() for item in findings],
        "write": _write_payload(write_result),
    }
    _render(payload, args.format)
    return 0


def _settings(args: argparse.Namespace) -> int:
    vault = _vault(args.vault)
    path = vault / CONFIG_NAME
    config = load_config(vault)
    changed = False
    if args.similarity_threshold is not None:
        config.semantic.threshold = args.similarity_threshold
        changed = True
    if args.inline_threshold is not None:
        config.auto_write.anchor_threshold = args.inline_threshold
        changed = True
    if args.limit is not None:
        config.semantic.limit = args.limit
        changed = True
    if args.clear_targets:
        config.target_paths = []
        changed = True
    for target in args.target:
        normalized = target.replace("\\", "/").strip().strip("/")
        if normalized and normalized.casefold() not in {item.casefold() for item in config.target_paths}:
            config.target_paths.append(normalized)
            changed = True
    target_removals = {item.replace("\\", "/").strip().strip("/").casefold() for item in args.remove_target}
    if target_removals:
        remaining_targets = [item for item in config.target_paths if item.casefold() not in target_removals]
        changed = changed or remaining_targets != config.target_paths
        config.target_paths = remaining_targets
    if args.clear_exclude_names:
        config.exclude_name_contains = []
        changed = True
    for fragment in args.exclude_name:
        if fragment and fragment.casefold() not in {item.casefold() for item in config.exclude_name_contains}:
            config.exclude_name_contains.append(fragment)
            changed = True
    removals = {item.casefold() for item in args.remove_exclude_name}
    if removals:
        remaining = [item for item in config.exclude_name_contains if item.casefold() not in removals]
        changed = changed or remaining != config.exclude_name_contains
        config.exclude_name_contains = remaining
    if args.auto_write is not None:
        config.auto_write.enabled = args.auto_write == AUTO_WRITE_ON
        changed = True
    if args.connections_heading is not None:
        config.auto_write.connections_heading = args.connections_heading
        changed = True
    config.validate()
    if changed:
        save_config(path, config)
        print(f"Settings saved: {path}")
    else:
        print(yaml.safe_dump(asdict(config), sort_keys=False, allow_unicode=True), end="")
    return 0


def _write_payload(result) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "transaction": result.transaction,
        "modified": list(result.modified),
        "links_added": result.links_added,
    }


def _render(payload: dict[str, Any], output_format: str) -> None:
    if output_format == FORMAT_JSON:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    index = payload.get("index")
    if index:
        print(
            f"Index: {index['embedded']} embedded, {index['reused']} reused, "
            f"{index['removed']} removed"
        )
    suggestions = payload.get("suggestions", [])
    print(f"Suggestions: {len(suggestions)}")
    for item in suggestions:
        print(f"  {item['source']} -> {item['target']}  {item['score']:.3f}")
    if "findings" in payload:
        findings = payload["findings"]
        print(f"Findings: {len(findings)}")
        for item in findings:
            target = f" -> {item['target']}" if item.get("target") else ""
            print(f"  {item['kind']}: {item['source']}{target}")
    write = payload.get("write")
    if write:
        print(f"Links added: {write['links_added']}; files modified: {len(write['modified'])}")


def _init(args: argparse.Namespace) -> int:
    vault = _vault(args.vault)
    config = VaultConfig()
    headings: Counter[str] = Counter()
    for note in discover_notes(vault, config):
        for line in note.text.splitlines():
            match = HEADING_RE.match(line)
            if match and any(word in match.group(2).casefold() for word in HEADING_KEYWORDS):
                headings[line.strip()] += 1
    if headings:
        config.auto_write.connections_heading = headings.most_common(1)[0][0]
    path = write_default_config(vault, overwrite=args.force, config=config)
    print(f"Configuration ready: {path}")
    return 0


def _undo(args: argparse.Namespace) -> int:
    vault = _vault(args.vault)
    result = undo_last(vault)
    payload = {
        "command": "undo",
        "transaction": result.transaction,
        "restored": list(result.modified),
    }
    if args.format == FORMAT_JSON:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif result.transaction:
        print(f"Restored {len(result.modified)} files from {result.transaction}")
    else:
        print("Nothing to undo")
    return 0


def doctor_checks(vault: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "vault_readable": {"ok": vault.exists() and vault.is_dir(), "value": str(vault)},
        "config": {"ok": True, "value": str(vault / CONFIG_NAME)},
    }
    for module in REQUIRED_MODULES:
        checks[module] = {"ok": importlib.util.find_spec(module) is not None}
    try:
        config = load_config(vault)
    except Exception as exc:
        checks["config"] = {"ok": False, "value": str(exc)}
        config = None
    if config is not None and config.semantic.index == "usearch":
        checks["usearch"] = {"ok": importlib.util.find_spec("usearch") is not None}
    return checks


def _audit(args: argparse.Namespace) -> int:
    vault = _vault(args.vault)
    config = load_config(vault)
    config.validate()
    progress = lambda message: print(message, file=sys.stderr)
    state = run_vault_audit(vault, config, progress=progress)
    payload = {
        "command": "audit",
        "findings": [item.as_dict() for item in state.get("audit_findings", [])],
    }
    _render(payload, args.format)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    vault = _vault(args.vault)
    checks = doctor_checks(vault)
    ok = all(check["ok"] for check in checks.values())
    if args.format == FORMAT_JSON:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2))
        return 0 if ok else 1
    for name, check in checks.items():
        status = "OK" if check["ok"] else "FAIL"
        value = f" — {check.get('value')}" if check.get("value") else ""
        print(f"{status:4} {name}{value}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "suggest":
            return _suggest(args)
        if args.command == "scan":
            return _scan(args)
        if args.command == "settings":
            return _settings(args)
        if args.command == "undo":
            return _undo(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "audit":
            return _audit(args)
        if args.command == "gui":
            if getattr(args, "web", False):
                from .ui_server import run_ui_server
                run_ui_server(port=args.port, open_browser=not args.no_browser)
            else:
                from .gui_app import run_gui_app
                run_gui_app()
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

