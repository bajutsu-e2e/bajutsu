"""Throwaway HTTP stub for the BE-0283 on-device network e2e — not shipped in any app.

The android `e2e-network` target runs this on the host so the showcase's OkHttp calls complete
deterministically: it answers `200 []` for every request. The emulator reaches it at
`10.0.2.2:<port>` (its alias to the host loopback), so binding `127.0.0.1` is enough. The point under
test is the collector transport (BajutsuNet → host collector over `adb reverse`), not this endpoint.
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def _ok(self) -> None:
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._ok()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # drain the request body before responding
        self._ok()

    def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
