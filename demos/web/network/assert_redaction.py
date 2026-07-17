#!/usr/bin/env python3
"""Assert the web network lane's real captured evidence is masked (BE-0282).

The `request` assertion in network.yaml proves interception + capture + the mocked provenance to
the deterministic runner, but the run grammar cannot assert on the *persisted* network.json — and
"is a secret in a really-captured header/body masked in the evidence" is exactly the gap BE-0282
closes on the cheap Linux lane. This script is that machine check: it reads the network.json a real
browser run produced and fails loudly (a non-zero exit) unless the mocked POST /api/sync exchange is
present, carries status 201 and `mocked: true`, and has both secrets — the Authorization header and
the `password` body field — masked, with the raw secret values absent from the whole file. No model
is consulted: this stays on the deterministic verdict path (prime directive 1).

Usage: python demos/web/network/assert_redaction.py <runs-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from bajutsu.redaction import PLACEHOLDER as _PLACEHOLDER

# The literal secrets the demo app (demos/web/app/index.html) sends on the Sync request. Kept in
# sync with that fetch: the header bearer token and the body `password` field value.
_HEADER_SECRET = "sk-demo-secret-token-abc123"
_BODY_SECRET = "hunter2-demo-secret"


def _fail(msg: str) -> NoReturn:
    print(f"redaction check FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _load_sync_exchange(runs_dir: Path) -> tuple[dict[str, Any], str]:
    """Return the single POST /api/sync exchange and the raw text of the file it came from.

    Scans every network.json under the run so a second matching exchange (e.g. another scenario
    tagged `network`) fails loudly rather than being silently masked by a first-match short-circuit.
    """
    files = sorted(runs_dir.rglob("network.json"))
    if not files:
        _fail(f"no network.json written under {runs_dir} — was the run network-enabled?")
    matches: list[tuple[dict[str, Any], str]] = []
    for path in files:
        raw = path.read_text(encoding="utf-8")
        exchanges = json.loads(raw)
        matches += [(ex, raw) for ex in exchanges if (ex.get("path") or "").endswith("/api/sync")]
    if len(matches) != 1:
        _fail(f"expected exactly one /api/sync exchange under {runs_dir}, found {len(matches)}")
    return matches[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _fail("usage: assert_redaction.py <runs-dir>")
    runs_dir = Path(argv[1])
    exchange, raw = _load_sync_exchange(runs_dir)

    if exchange.get("mocked") is not True:
        _fail(f"the /api/sync exchange is not marked mocked: {exchange.get('mocked')!r}")
    if exchange.get("status") != 201:
        _fail(f"expected the mock's 201 status, got {exchange.get('status')!r}")

    # Playwright lowercases request header names, so match case-insensitively.
    headers = {str(k).lower(): v for k, v in (exchange.get("requestHeaders") or {}).items()}
    auth = headers.get("authorization")
    if auth != _PLACEHOLDER:
        _fail(f"Authorization header not masked: {auth!r}")
    body = exchange.get("requestBody") or ""
    if _BODY_SECRET in body:
        _fail("the password body field leaked its value into network.json")
    if _PLACEHOLDER not in body:
        _fail(f"the password body field was not masked with {_PLACEHOLDER}: {body!r}")

    # Belt and braces: no raw secret survives anywhere in the persisted file.
    for secret in (_HEADER_SECRET, _BODY_SECRET):
        if secret in raw:
            _fail(f"raw secret {secret!r} present in network.json")

    print("redaction check passed: /api/sync captured, mocked, and secrets masked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
