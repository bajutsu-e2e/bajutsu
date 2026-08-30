#!/usr/bin/env python3
"""Serve the showcase browser fixture over HTTPS, for Password AutoFill's associated-domain check.

iOS offers to save a credential typed into an app's *own* fields only for an app with a verified
`webcredentials:` associated domain, and verifying one means fetching
`/.well-known/apple-app-site-association` over HTTPS from the named host. Measured, declaring the
entitlement alone is not enough: without this server and a certificate authority the Simulator
trusts, no prompt appears at all. The in-app-browser route needs none of it, which is why only
`e2e-savepassword-native` stands this up.

The `?mode=developer` in the entitlement is what makes iOS fetch the association straight from the
host rather than through Apple's content delivery network, so a host that exists only on this machine
is reachable. `localtest.me` resolves to 127.0.0.1 in public DNS, so the Simulator reaches the Mac
with no `/etc/hosts` edit.

The certificate and its authority are generated per run into a gitignored directory and thrown away
afterwards (see `e2e-savepassword-native`); nothing here is a committed key.
"""

from __future__ import annotations

import functools
import http.server
import ssl
import sys


class _Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # Apple's fetcher requires the association to be served as JSON. The file has no extension,
        # so the default type guess would make it `application/octet-stream` and the check would fail.
        if self.path.endswith("apple-app-site-association"):
            self.send_header("Content-Type", "application/json")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")
        sys.stderr.flush()


def main() -> None:
    root, port, cert = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    handler = functools.partial(_Handler, directory=root)
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print(f"serving {root} on https://0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
