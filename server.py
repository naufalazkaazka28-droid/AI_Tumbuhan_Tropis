#!/usr/bin/env python3
"""Serve the browser-only chatbot and its local static assets."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
STATIC_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/tropical_plants_kb.json": ("tropical_plants_kb.json", "application/json; charset=utf-8"),
    "/background.jpg": ("background.jpg", "image/jpeg"),
    "/logo.png": ("logo.png", "image/png"),
    "/chat-decoration.svg": ("chat-decoration.svg", "image/svg+xml"),
}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _serve_static(self, include_body):
        entry = STATIC_FILES.get(self.path.split("?", 1)[0])
        if entry is None:
            self.send_error(404, "Not found")
            return
        name, content_type = entry
        try:
            content = (STATIC_DIR / name).read_bytes()
        except OSError:
            self.send_error(500, "Static file could not be read")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if include_body:
            self.wfile.write(content)

    def do_GET(self):
        self._serve_static(include_body=True)

    def do_HEAD(self):
        self._serve_static(include_body=False)

    def do_POST(self):
        self.send_error(404, "No API endpoints are available")

    def do_PUT(self):
        self.send_error(404, "No API endpoints are available")

    def do_DELETE(self):
        self.send_error(404, "No API endpoints are available")


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Chatbot tumbuhan tropis lokal berjalan di http://{HOST}:{PORT}")
    print("Pemrosesan pesan berlangsung di browser; server hanya menyajikan berkas statis.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
