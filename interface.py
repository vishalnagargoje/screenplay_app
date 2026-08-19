"""
interface.py
------------
SCRIPT 4 of 4.

Serves the local dashboard UI (static/index.html) that displays everything
stored in the database, and lets you add/remove props, vehicles, and
costumes per scene. Talks to api.py over HTTP (so api.py must be running).

Deliberately dependency-free (uses only Python's built-in http.server) so
this script has no coupling to FastAPI/SQLAlchemy at all — it's a pure
static-file server; all data comes from fetch() calls in the browser to
the API server.

Usage:
    Terminal 1:  python api.py          (backend,  http://127.0.0.1:8000)
    Terminal 2:  python interface.py    (frontend, http://127.0.0.1:3000)
    Then open http://127.0.0.1:3000 in your browser.
"""
import http.server
import socketserver
import webbrowser
from pathlib import Path

PORT = 3000
STATIC_DIR = Path(__file__).resolve().parent / "static"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        pass  # keep the terminal quiet; errors still surface as HTTP responses


def main():
    if not (STATIC_DIR / "index.html").exists():
        raise SystemExit(f"static/index.html not found in {STATIC_DIR}")

    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://127.0.0.1:{PORT}"
        print(f"Interface running at {url}")
        print("Make sure api.py is running in another terminal (http://127.0.0.1:8000).")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
