"""
PLACEHOLDER agent-template — Dev B owns this file's real contents (PRD 4.2/4.3/4.4).

This stub exists only so the Provisioner can be built and tested end-to-end
before the real sandbox agent lands. It implements the minimum runtime
contract declared in forge.manifest.json: GET /health, GET /, POST /chat,
reading PORT from env and spec.json from the working directory.

Dev B: replace this file's contents freely. Keep the contract (manifest +
these three routes) so the Provisioner doesn't need to change.
"""
import json
import os
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer

PORT = int(os.environ.get("PORT", "8000"))

try:
    with open("spec.json", "r", encoding="utf-8") as f:
        SPEC = json.load(f)
except FileNotFoundError:
    SPEC = {}

BUSINESS_NAME = (
    SPEC.get("business_name")
    or (SPEC.get("business") or {}).get("name")
    or ((SPEC.get("products") or {}).get("store") or {}).get("name")
    or "unknown business"
)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/":
            body = f"<h1>Placeholder agent for {BUSINESS_NAME}</h1><p>Dev B's real chat page goes here.</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/chat":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            self._send_json(200, {
                "reply": f"(placeholder) {BUSINESS_NAME} agent received: {payload.get('message', '')}",
            })
            return
        self._send_json(404, {"error": "not_found"})

    def log_message(self, format, *args):
        pass


with TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"placeholder agent listening on {PORT}", flush=True)
    httpd.serve_forever()
