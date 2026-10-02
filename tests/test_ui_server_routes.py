from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from zettellinker.config import VaultConfig, save_config
from zettellinker.ui_server import ZettelLinkerRequestHandler


class DummySocket:
    def __init__(self, request_bytes: bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return self.rfile
        if "w" in mode or "b" in mode:
            return self.wfile
        return self.wfile

    def sendall(self, data: bytes) -> None:
        self.wfile.write(data)


class DummyServer:
    def __init__(self):
        self.server_name = "localhost"
        self.server_port = 8765


def request(method: str, path: str, payload: dict | str | None = None) -> tuple[int, dict | str]:
    if payload is not None:
        raw_body = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) else payload.encode("utf-8")
        headers = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(raw_body)}\r\n\r\n"
        ).encode("utf-8")
        req_bytes = headers + raw_body
    else:
        req_bytes = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("utf-8")

    sock = DummySocket(req_bytes)
    server = DummyServer()
    ZettelLinkerRequestHandler(sock, ("127.0.0.1", 8765), server)
    raw_output = sock.wfile.getvalue().decode("utf-8")

    status_line = raw_output.splitlines()[0] if raw_output else ""
    status_code = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 500

    parts = raw_output.split("\r\n\r\n", 1)
    body_text = parts[1] if len(parts) > 1 else ""
    try:
        return status_code, json.loads(body_text)
    except json.JSONDecodeError:
        return status_code, body_text


class UIServerRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "NoteA.md").write_text("Fruit nutrition and health benefits.", encoding="utf-8")
        (self.vault / "NoteB.md").write_text("Fruit health nutrition.", encoding="utf-8")
        save_config(self.vault / ".zettellinker.yml", VaultConfig())

    def tearDown(self):
        self.temp.cleanup()

    def test_get_root_serves_html(self):
        status, body = request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<title>ZettelLinker", body)
        self.assertIn("<!DOCTYPE html>", body)

    def test_get_config(self):
        status, data = request("GET", f"/api/config?vault={self.vault}")
        self.assertEqual(status, 200)
        self.assertEqual(Path(data["vault"]).resolve(), self.vault.resolve())
        self.assertEqual(data["semantic"]["threshold"], 0.6)

    def test_post_settings_updates_and_persists(self):
        status, data = request(
            "POST",
            "/api/settings",
            {"vault": str(self.vault), "similarity_threshold": 0.77, "auto_write": "on"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))

        status_get, conf = request("GET", f"/api/config?vault={self.vault}")
        self.assertEqual(conf["semantic"]["threshold"], 0.77)
        self.assertTrue(conf["auto_write"]["enabled"])

    def test_post_scan_full_pipeline(self):
        old = os.environ.get("ZETTELLINKER_TEST_EMBEDDER")
        os.environ["ZETTELLINKER_TEST_EMBEDDER"] = "1"
        try:
            status, data = request("POST", "/api/scan", {"vault": str(self.vault)})
            self.assertEqual(status, 200)
            self.assertEqual(Path(data["vault"]).resolve(), self.vault.resolve())
            self.assertIn("suggestions", data)
            self.assertIn("findings", data)
            self.assertIn("index", data)
            self.assertGreaterEqual(len(data["suggestions"]), 1)
        finally:
            if old is None:
                os.environ.pop("ZETTELLINKER_TEST_EMBEDDER", None)
            else:
                os.environ["ZETTELLINKER_TEST_EMBEDDER"] = old

    def test_post_suggest_for_single_note(self):
        old = os.environ.get("ZETTELLINKER_TEST_EMBEDDER")
        os.environ["ZETTELLINKER_TEST_EMBEDDER"] = "1"
        try:
            status, data = request("POST", "/api/suggest", {"vault": str(self.vault), "note": "NoteA"})
            self.assertEqual(status, 200)
            self.assertEqual(data["note"], "NoteA")
            self.assertIn("suggestions", data)
        finally:
            if old is None:
                os.environ.pop("ZETTELLINKER_TEST_EMBEDDER", None)
            else:
                os.environ["ZETTELLINKER_TEST_EMBEDDER"] = old

    def test_post_suggest_empty_note_returns_400(self):
        status, data = request("POST", "/api/suggest", {"vault": str(self.vault), "note": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_post_undo_endpoint(self):
        status, data = request("POST", "/api/undo", {"vault": str(self.vault)})
        self.assertEqual(status, 200)
        self.assertIn("transaction", data)
        self.assertIn("restored", data)

    def test_get_doctor_endpoint(self):
        status, data = request("GET", f"/api/doctor?vault={self.vault}")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        checks = data["checks"]
        self.assertTrue(checks["python"]["ok"])
        self.assertTrue(checks["sentence_transformers"]["ok"])
        self.assertTrue(checks["langchain"]["ok"])
        self.assertTrue(checks["langgraph"]["ok"])

    def test_unknown_route_returns_404(self):
        status, data = request("GET", "/api/unsupported_path")
        self.assertEqual(status, 404)
        self.assertEqual(data.get("error"), "Not Found")
