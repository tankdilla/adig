import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.smoke import run_smoke_suite, wait_for_http_ready


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/login":
            body = b"<html><body><form><input name='username'><input name='token'></form></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/":
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _start_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_wait_for_http_ready_and_run_smoke_suite():
    server, thread = _start_server()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        ready, message = wait_for_http_ready(f"{base_url}/health", timeout=3.0, interval=0.1)
        assert ready, message

        results = run_smoke_suite(base_url, timeout=2.0)
        assert all(result.ok for result in results)
        assert [result.path for result in results] == ["/health", "/login", "/"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
