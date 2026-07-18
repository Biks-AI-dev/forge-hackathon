"""Minimal HTTP server written INTO the sandbox by the PoC. Not part of the real agent."""
import http.server
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok" if self.path == "/health" else b"daytona poc sandbox is alive"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"listening on {PORT}", flush=True)
    httpd.serve_forever()
