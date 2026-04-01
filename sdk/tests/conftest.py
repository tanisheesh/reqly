import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _CapturingHandler(BaseHTTPRequestHandler):
    received_batches: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        _CapturingHandler.received_batches.append(json.loads(body))
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"accepted": 1, "rejected": 0}')

    def log_message(self, format, *args):  # noqa: A002 - quiet test logs
        pass


@pytest.fixture
def fake_collector():
    _CapturingHandler.received_batches = []
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", _CapturingHandler.received_batches
    finally:
        server.shutdown()
        server.server_close()
