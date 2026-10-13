"""Read-only preview of the HTML interface. Sending runs in the browser extension."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

import linkconnect as runner


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, status, data, mime="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.reply(403, b"Local preview only.")
            return
        path = urlsplit(self.path).path
        assets = {"/": ("web/index.html", "text/html; charset=utf-8"),
                  "/index.html": ("web/index.html", "text/html; charset=utf-8"),
                  "/INSTALL.html": ("INSTALL.html", "text/html; charset=utf-8")}
        for name in ["app.js", "profile.js", "runner.js", "style.css"]:
            assets["/" + name] = ("web/" + name, "text/css; charset=utf-8" if name.endswith("css") else "text/javascript; charset=utf-8")
        if path not in assets:
            self.reply(404, b"Not found.")
            return
        filename, mime = assets[path]
        self.reply(200, (runner.ROOT / filename).read_bytes(), mime)

    def do_POST(self):
        self.reply(405, b"Use the installed browser extension. This preview cannot log in or send requests.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    server = LocalServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Read-only UI preview: {url}. Use the extension toolbar to send requests.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
