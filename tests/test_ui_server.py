from __future__ import annotations

import io
import json
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


def make_handler(request_bytes: bytes) -> tuple[ZettelLinkerRequestHandler, str]:
    sock = DummySocket(request_bytes)
    server = DummyServer()
    handler = ZettelLinkerRequestHandler(sock, ("127.0.0.1", 8765), server)
    output = sock.wfile.getvalue().decode("utf-8")
    return handler, output


class UIServerDirectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "NoteA.md").write_text("Fruit nutrition and health.", encoding="utf-8")
        (self.vault / "NoteB.md").write_text("Fruit health benefits.", encoding="utf-8")
        save_config(self.vault / ".zettellinker.yml", VaultConfig())

    def tearDown(self):
        self.temp.cleanup()

    def test_get_index_html(self):
        req = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        _, output = make_handler(req)
        self.assertIn("200 OK", output)
        self.assertIn("<title>ZettelLinker", output)
        self.assertIn("ZettelLinker", output)

    def test_get_config(self):
        path = f"/api/config?vault={self.vault}"
        req = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("utf-8")
        _, output = make_handler(req)
        self.assertIn("200 OK", output)
        self.assertIn(str(self.vault), output)
        self.assertIn("0.6", output)

    def test_post_settings(self):
        payload = json.dumps({
            "vault": str(self.vault),
            "similarity_threshold": 0.68,
            "auto_write": "off"
        }).encode("utf-8")
        headers = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode("utf-8")
        req = headers + payload
        _, output = make_handler(req)
        self.assertIn("200 OK", output)
        self.assertIn('"ok": true', output)
