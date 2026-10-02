from __future__ import annotations

import json
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .cli import doctor_checks
from .config import CONFIG_NAME, load_config, save_config
from .workflow import run_note_suggest, run_vault_scan
from .writer import undo_last


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ZettelLinker</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #111;
            color: #ddd;
            padding: 20px;
            max-width: 960px;
            margin: 0 auto;
        }
        h1 { font-size: 18px; font-weight: 600; color: #fff; margin-bottom: 16px; }
        h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: #888; margin-bottom: 10px; }
        .box {
            background: #1a1a1a;
            border: 1px solid #2e2e2e;
            border-radius: 6px;
            padding: 14px;
            margin-bottom: 16px;
        }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
        .field { margin-bottom: 10px; }
        label { display: block; font-size: 11px; font-weight: 600; color: #999; margin-bottom: 4px; text-transform: uppercase; }
        input[type="text"], input[type="number"], select {
            width: 100%;
            background: #0d0d0d;
            border: 1px solid #333;
            border-radius: 4px;
            padding: 7px 10px;
            color: #fff;
            font-size: 13px;
            font-family: inherit;
        }
        input:focus, select:focus { outline: none; border-color: #2563eb; }
        .row { display: flex; gap: 8px; align-items: center; }
        button {
            padding: 7px 14px;
            background: #2563eb;
            color: #fff;
            border: none;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
        }
        button:hover { background: #1d4ed8; }
        button.secondary { background: #2a2a2a; border: 1px solid #3d3d3d; color: #ccc; }
        button.secondary:hover { background: #333; color: #fff; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 6px; }
        th { text-align: left; padding: 8px 10px; color: #888; font-weight: 500; border-bottom: 1px solid #333; font-size: 11px; text-transform: uppercase; }
        td { padding: 8px 10px; border-bottom: 1px solid #222; }
        .score { font-family: monospace; font-size: 12px; color: #60a5fa; }
        #log {
            background: #080808;
            border: 1px solid #222;
            border-radius: 4px;
            padding: 10px;
            font-family: monospace;
            font-size: 11px;
            height: 120px;
            overflow-y: auto;
            color: #4ade80;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <h1>ZettelLinker</h1>

    <div class="box">
        <h2>Vault</h2>
        <div class="row">
            <input type="text" id="vaultPath" style="flex:1;">
            <input type="file" id="filePicker" webkitdirectory directory style="display:none;" onchange="handleFilePicker(this)">
            <button class="secondary" onclick="browseVault()">Browse</button>
            <button class="secondary" onclick="loadConfig()">Load</button>
        </div>
    </div>

    <div class="box">
        <h2>Actions</h2>
        <div class="row" style="margin-bottom: 10px;">
            <button id="btnScan" onclick="runScan()">Scan Vault</button>
            <button class="secondary" onclick="runUndo()">Undo</button>
            <button class="secondary" onclick="runDoctor()">Doctor</button>
        </div>
        <div class="row">
            <input type="text" id="suggestNote" style="flex:1;">
            <button class="secondary" onclick="runSuggest()">Suggest</button>
        </div>
    </div>

    <div class="box">
        <h2>Settings</h2>
        <div class="grid">
            <div class="field">
                <label>Threshold</label>
                <input type="number" id="simThreshold" step="0.05" min="0" max="1" value="0.60">
            </div>
            <div class="field">
                <label>Inline Threshold</label>
                <input type="number" id="inlineThreshold" step="0.05" min="0" max="1" value="0.72">
            </div>
            <div class="field">
                <label>Limit</label>
                <input type="number" id="limit" min="1" value="5">
            </div>
            <div class="field">
                <label>Auto-Write</label>
                <select id="autoWrite">
                    <option value="off">Off</option>
                    <option value="on">On</option>
                </select>
            </div>
            <div class="field">
                <label>Heading</label>
                <input type="text" id="heading" value="## Connections">
            </div>
        </div>
        <button class="secondary" style="margin-top: 6px;" onclick="saveSettings()">Save Settings</button>
    </div>

    <div class="box">
        <h2>Suggestions</h2>
        <table>
            <thead>
                <tr>
                    <th>Source</th>
                    <th>Target</th>
                    <th>Score</th>
                </tr>
            </thead>
            <tbody id="suggestionsBody">
                <tr><td colspan="3" style="color:#555;">No suggestions.</td></tr>
            </tbody>
        </table>
    </div>

    <div class="box">
        <div class="row" style="justify-content: space-between; margin-bottom: 6px;">
            <h2>Log</h2>
            <button class="secondary" style="padding: 2px 6px; font-size: 10px;" onclick="clearLog()">Clear</button>
        </div>
        <div id="log">[Ready]</div>
    </div>

    <script>
        function log(msg) {
            const el = document.getElementById('log');
            el.innerText += '\n' + msg;
            el.scrollTop = el.scrollHeight;
        }

        function clearLog() {
            document.getElementById('log').innerText = '[Ready]';
        }

        function getVault() {
            return document.getElementById('vaultPath').value.trim();
        }

        function handleFilePicker(input) {
            if (input.files && input.files.length > 0) {
                const first = input.files[0];
                let path = first.path || '';
                if (path) {
                    const dir = path.substring(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')));
                    document.getElementById('vaultPath').value = dir;
                    loadConfig();
                }
            }
        }

        async function browseVault() {
            log('Opening file explorer...');
            const picker = document.getElementById('filePicker');
            if (picker) picker.click();
            try {
                const res = await fetch('/api/browse', { method: 'POST' });
                const data = await res.json();
                if (data.vault) {
                    document.getElementById('vaultPath').value = data.vault;
                    loadConfig();
                }
            } catch (e) {
                log('Browse notice: ' + e);
            }
        }

        async function loadConfig() {
            let vault = getVault();
            log('Loading config for: ' + (vault || 'default folder'));
            try {
                const res = await fetch('/api/config?vault=' + encodeURIComponent(vault));
                const data = await res.json();
                if (data.error) {
                    log('Error: ' + data.error);
                    return;
                }
                document.getElementById('vaultPath').value = data.vault;
                document.getElementById('simThreshold').value = data.semantic.threshold;
                document.getElementById('inlineThreshold').value = data.auto_write.anchor_threshold;
                document.getElementById('limit').value = data.semantic.limit;
                document.getElementById('autoWrite').value = data.auto_write.enabled ? 'on' : 'off';
                document.getElementById('heading').value = data.auto_write.connections_heading;
                log('Loaded: ' + data.vault);
            } catch (e) {
                log('Error: ' + e);
            }
        }

        async function saveSettings() {
            const payload = {
                vault: getVault(),
                similarity_threshold: parseFloat(document.getElementById('simThreshold').value),
                inline_threshold: parseFloat(document.getElementById('inlineThreshold').value),
                limit: parseInt(document.getElementById('limit').value),
                auto_write: document.getElementById('autoWrite').value,
                connections_heading: document.getElementById('heading').value
            };
            log('Saving settings...');
            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.error) {
                    log('Error: ' + data.error);
                } else {
                    log('Settings saved.');
                }
            } catch (e) {
                log('Error: ' + e);
            }
        }

        function renderSuggestions(items) {
            const tbody = document.getElementById('suggestionsBody');
            if (!items || items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="color:#555;">No suggestions found.</td></tr>';
                return;
            }
            tbody.innerHTML = items.map(s => `
                <tr>
                    <td>${s.source}</td>
                    <td>${s.target}</td>
                    <td><span class="score">${(s.score * 100).toFixed(1)}%</span></td>
                </tr>
            `).join('');
        }

        async function runScan() {
            log('Scanning...');
            const btn = document.getElementById('btnScan');
            btn.disabled = true;
            try {
                const res = await fetch('/api/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ vault: getVault() })
                });
                const data = await res.json();
                if (data.error) {
                    log('Error: ' + data.error);
                } else {
                    log(`Scan done. Suggestions: ${data.suggestions.length}`);
                    renderSuggestions(data.suggestions);
                }
            } catch (e) {
                log('Error: ' + e);
            } finally {
                btn.disabled = false;
            }
        }

        async function runSuggest() {
            const note = document.getElementById('suggestNote').value.trim();
            if (!note) {
                log('Note name required.');
                return;
            }
            log('Suggesting for: ' + note);
            try {
                const res = await fetch('/api/suggest', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ vault: getVault(), note: note })
                });
                const data = await res.json();
                if (data.error) {
                    log('Error: ' + data.error);
                } else {
                    log(`Suggestions for ${data.note}: ${data.suggestions.length}`);
                    renderSuggestions(data.suggestions);
                }
            } catch (e) {
                log('Error: ' + e);
            }
        }

        async function runUndo() {
            log('Undoing...');
            try {
                const res = await fetch('/api/undo', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ vault: getVault() })
                });
                const data = await res.json();
                if (data.error) {
                    log('Error: ' + data.error);
                } else if (data.transaction) {
                    log(`Restored ${data.restored.length} files.`);
                } else {
                    log('Nothing to undo.');
                }
            } catch (e) {
                log('Error: ' + e);
            }
        }

        async function runDoctor() {
            log('Checking...');
            try {
                const res = await fetch('/api/doctor?vault=' + encodeURIComponent(getVault()));
                const data = await res.json();
                log('Doctor: ' + (data.ok ? 'OK' : 'FAIL'));
                for (let [k, v] of Object.entries(data.checks)) {
                    log(`  ${v.ok ? 'OK' : 'FAIL'}: ${k}`);
                }
            } catch (e) {
                log('Error: ' + e);
            }
        }

        loadConfig();
    </script>
</body>
</html>
"""


class ZettelLinkerRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Silence default stderr logging to keep console clean
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_CONTENT)
            return

        if parsed.path == "/api/config":
            params = urllib.parse.parse_qs(parsed.query)
            vault_str = params.get("vault", [""])[0]
            vault = Path(vault_str).expanduser().resolve() if vault_str else Path.cwd()
            try:
                config = load_config(vault)
                self._send_json({
                    "vault": str(vault),
                    "semantic": {"threshold": config.semantic.threshold, "limit": config.semantic.limit},
                    "auto_write": {"enabled": config.auto_write.enabled, "anchor_threshold": config.auto_write.anchor_threshold, "connections_heading": config.auto_write.connections_heading},
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/doctor":
            params = urllib.parse.parse_qs(parsed.query)
            vault_str = params.get("vault", [""])[0]
            vault = Path(vault_str).expanduser().resolve() if vault_str else Path.cwd()
            checks = doctor_checks(vault)
            ok = all(check["ok"] for check in checks.values())
            self._send_json({"ok": ok, "checks": checks})
            return

        self._send_json({"error": "Not Found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/browse":
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                folder = filedialog.askdirectory(title="Select Obsidian Vault Folder")
                root.destroy()
                if folder:
                    self._send_json({"vault": folder})
                else:
                    self._send_json({"cancelled": True})
            except Exception:
                try:
                    import platform, subprocess
                    system = platform.system()
                    folder = None
                    if system == "Darwin":
                        cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select Obsidian Vault Folder")']
                        proc = subprocess.run(cmd, capture_output=True, text=True)
                        if proc.returncode == 0 and proc.stdout.strip():
                            folder = proc.stdout.strip()
                    elif system == "Windows":
                        cmd = ["powershell", "-Command", "Add-Type -AssemblyName System.Windows.Forms; $dialog = New-Object System.Windows.Forms.FolderBrowserDialog; if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"]
                        proc = subprocess.run(cmd, capture_output=True, text=True)
                        if proc.returncode == 0 and proc.stdout.strip():
                            folder = proc.stdout.strip()
                    else:
                        cmd = ["zenity", "--file-selection", "--directory", "--title=Select Obsidian Vault Folder"]
                        proc = subprocess.run(cmd, capture_output=True, text=True)
                        if proc.returncode == 0 and proc.stdout.strip():
                            folder = proc.stdout.strip()

                    if folder:
                        self._send_json({"vault": folder})
                    else:
                        self._send_json({"cancelled": True})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_data = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw_data.decode("utf-8"))
        except json.JSONDecodeError:
            data = {}

        vault_str = data.get("vault", "")
        vault = Path(vault_str).expanduser().resolve() if vault_str else Path.cwd()

        if parsed.path == "/api/settings":
            try:
                config = load_config(vault)
                if "similarity_threshold" in data:
                    config.semantic.threshold = float(data["similarity_threshold"])
                if "inline_threshold" in data:
                    config.auto_write.anchor_threshold = float(data["inline_threshold"])
                if "limit" in data:
                    config.semantic.limit = int(data["limit"])
                if "auto_write" in data:
                    config.auto_write.enabled = data["auto_write"] == "on"
                if "connections_heading" in data:
                    config.auto_write.connections_heading = str(data["connections_heading"])
                config.validate()
                save_config(vault / CONFIG_NAME, config)
                self._send_json({"ok": True, "vault": str(vault)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/scan":
            try:
                config = load_config(vault)
                state = run_vault_scan(vault, config)
                suggestions = state.get("suggestions", [])
                findings = state.get("audit_findings", [])
                stats = state.get("index_stats")
                stats_dict = stats.as_dict() if stats else {"embedded": 0, "reused": 0, "removed": 0}
                write_result = state.get("write_result")
                self._send_json({
                    "vault": str(vault),
                    "index": stats_dict,
                    "suggestions": [item.as_dict() for item in suggestions],
                    "findings": [item.as_dict() for item in findings],
                    "write": {
                        "transaction": write_result.transaction,
                        "modified": list(write_result.modified),
                        "links_added": write_result.links_added,
                    } if write_result else None,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/suggest":
            note_param = data.get("note", "")
            if not note_param:
                self._send_json({"error": "Note title or path is required"}, status=400)
                return
            try:
                config = load_config(vault)
                state = run_note_suggest(vault, config, note_param)
                suggestions = state.get("suggestions", [])
                stats = state.get("index_stats")
                stats_dict = stats.as_dict() if stats else {"embedded": 0, "reused": 0, "removed": 0}
                self._send_json({
                    "vault": str(vault),
                    "note": state["note"],
                    "index": stats_dict,
                    "suggestions": [item.as_dict() for item in suggestions],
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/undo":
            try:
                result = undo_last(vault)
                self._send_json({
                    "transaction": result.transaction,
                    "restored": list(result.modified),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "Not Found"}, status=404)


def run_ui_server(port: int = 8765, open_browser: bool = True) -> None:
    server = HTTPServer(("127.0.0.1", port), ZettelLinkerRequestHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"ZettelLinker GUI server running at: {url}", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down ZettelLinker GUI server.", flush=True)
        server.server_close()
