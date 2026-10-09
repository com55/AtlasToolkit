"""Serve www/ with the app version injected into index.html.

Reads version from pyproject.toml (same source as the desktop title).
Does not write the repo copy of index.html.

  uv run python scripts/serve_www.py
  uv run python scripts/serve_www.py --port 8000
"""
from __future__ import annotations

import argparse
import http.server
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from web_version import load_version, stamp_index_html  # noqa: E402

WWW = ROOT / "www"


class StampingHandler(http.server.SimpleHTTPRequestHandler):
    version = "0.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WWW), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = stamp_index_html(
                (WWW / "index.html").read_text(encoding="utf-8"),
                self.version,
            )
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    version = load_version()
    StampingHandler.version = version
    server = http.server.ThreadingHTTPServer((args.bind, args.port), StampingHandler)
    print(f"Serving {WWW} at http://{args.bind}:{args.port}/  (Atlas Toolkit v{version})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
