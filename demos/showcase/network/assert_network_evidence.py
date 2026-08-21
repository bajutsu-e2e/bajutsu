#!/usr/bin/env python3
"""Assert the iOS network lane's real captured evidence is masked and correctly attributed (BE-0282).

The `request` assertions in network_mock.yaml / network_live.yaml prove interception and capture to
the deterministic runner, but the run grammar cannot assert on the *persisted* network.json — and
"is a secret in a really-captured header/body masked in the evidence" is exactly the gap BE-0282
closes, here on the BajutsuKit → loopback POST → collector path rather than the browser's. This
script is that machine check over what a real Simulator run wrote: it fails loudly (a non-zero exit)
unless the mocked `POST /post` exchange is present, carries status 201 and `mocked: true`, and has
both secrets — the Authorization header and the `password` body field — masked, with the raw secret
values absent from the whole file.

It also checks the *live* `GET /horses` exchange carries `mocked` false — the half the web twin
(demos/web/network/assert_redaction.py) cannot make, holding only mocked traffic. Be exact about what
each leg proves, because the two are not symmetric. BajutsuKit sends the key only when a stub served
the request (`BajutsuNet.report`: `if mocked { payload["mocked"] = true }`) and
`NetworkExchange.mocked` defaults to False, so a recorded `true` cannot be a default — that is the
mocked leg's own strength, and it stands alone. The live leg proves the complement: no stub
over-matched a request nothing stubbed. It does not prove the app reported false, because the app
never does. No model is consulted here either — this stays on the deterministic verdict path
(prime directive 1).

Usage: python demos/showcase/network/assert_network_evidence.py <runs-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from bajutsu.evidence.redaction import PLACEHOLDER as _PLACEHOLDER

# The literal secrets the SwiftUI showcase sends on the Log submit
# (ios/swiftui/Sources/LogView.swift): the header bearer token and the body `password` field value.
# SwiftUI only — the UIKit twin's submit (ios/uikit/Sources/LogController.swift) carries neither, so
# the lane pins `--target showcase-swiftui` (demos/showcase/Makefile) rather than running both.
_HEADER_SECRET = "demo-secret-abc123"
_BODY_SECRET = "hunter2"

# A non-secret key of the same submit body, so over-redaction is distinguishable from redaction.
_KEPT_FIELD = '"note"'

# The two exchanges the lane's scenarios produce: the mocked Log submit and the observed catalog GET.
_MOCK_PATH = "/post"
_LIVE_PATH = "/horses"


def _fail(msg: str) -> NoReturn:
    print(f"network evidence check FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _load(runs_dir: Path) -> tuple[list[dict[str, Any]], str]:
    """Return every exchange written under the run, and the concatenated raw text of its files.

    Both scenarios write their own `<sid>/network.json`, so the caller selects by path across the
    whole run. The raw text is kept for the "no secret survives anywhere" sweep at the end.
    """
    files = sorted(runs_dir.rglob("network.json"))
    if not files:
        _fail(f"no network.json written under {runs_dir} — was the run network-enabled?")
    exchanges: list[dict[str, Any]] = []
    raw = ""
    for path in files:
        text = path.read_text(encoding="utf-8")
        raw += text
        exchanges += json.loads(text)
    return exchanges, raw


def _sole(exchanges: list[dict[str, Any]], suffix: str) -> dict[str, Any]:
    """The single exchange whose path ends in *suffix*.

    Exactly one, so a second matching exchange (a retried scenario, a newly added one) fails loudly
    rather than being masked by a first-match short-circuit.
    """
    matches = [ex for ex in exchanges if (ex.get("path") or "").endswith(suffix)]
    if len(matches) != 1:
        _fail(f"expected exactly one {suffix} exchange, found {len(matches)}")
    return matches[0]


def _check_mocked_submit(exchanges: list[dict[str, Any]]) -> None:
    """The mocked Log submit: served by the stub, and both of its secrets masked.

    Selects its own exchange rather than taking one, so the two checks below cannot be handed each
    other's — both take the same type, and only the call order would have bound them.
    """
    exchange = _sole(exchanges, _MOCK_PATH)
    if exchange.get("mocked") is not True:
        _fail(f"the {_MOCK_PATH} exchange is not marked mocked: {exchange.get('mocked')!r}")
    if exchange.get("status") != 201:
        _fail(f"expected the mock's 201 status, got {exchange.get('status')!r}")

    # Matched case-insensitively: the header name a URLProtocol reports is whatever the app set, and
    # the collector stores it verbatim. The failure messages never echo the header value, the body, or
    # the secret literal — a redaction checker that printed the unmasked content on failure would
    # itself leak it (CodeQL: clear-text logging).
    headers = {str(k).lower(): v for k, v in (exchange.get("requestHeaders") or {}).items()}
    if headers.get("authorization") != _PLACEHOLDER:
        _fail(f"Authorization header not masked to {_PLACEHOLDER}")
    body = exchange.get("requestBody") or ""
    if _BODY_SECRET in body:
        _fail("the password body field leaked its value into network.json")
    if _PLACEHOLDER not in body:
        _fail(f"the password body field was not masked to {_PLACEHOLDER}")
    # Redaction has to be surgical, not total: a redactor that replaced the *whole* body with the
    # placeholder would satisfy both checks above while destroying the evidence they exist to
    # validate. `note` is the submit's own non-secret field, so its key surviving is what separates
    # "masked the secret" from "masked everything".
    if _KEPT_FIELD not in body:
        _fail(f"the non-secret {_KEPT_FIELD} field did not survive redaction")


def _check_observed_catalog(exchanges: list[dict[str, Any]]) -> None:
    """The live catalog GET: nothing stubbed it, so no over-broad mock matcher may have claimed it.

    An absent key fails as well, but for a different reason than a wrong one: the app never sends
    `mocked` false (see the module docstring), so this rule rests on the persisted evidence
    materializing the field, which `tests/test_showcase_network_demo.py` pins.
    """
    exchange = _sole(exchanges, _LIVE_PATH)
    if exchange.get("mocked") is not False:
        _fail(
            f"the observed {_LIVE_PATH} exchange carries mocked={exchange.get('mocked')!r}; "
            "no stub served it, so a stub must not have claimed it"
        )
    if (exchange.get("method") or "").upper() != "GET":
        _fail(f"expected the catalog GET, got method {exchange.get('method')!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _fail("usage: assert_network_evidence.py <runs-dir>")
    runs_dir = Path(argv[1])
    exchanges, raw = _load(runs_dir)

    _check_mocked_submit(exchanges)
    _check_observed_catalog(exchanges)

    # Belt and braces: no raw secret survives anywhere in the persisted files.
    for name, secret in (("Authorization header", _HEADER_SECRET), ("password body", _BODY_SECRET)):
        if secret in raw:
            _fail(f"the {name} secret survived unmasked in network.json")

    print(
        f"network evidence check passed: {_MOCK_PATH} mocked with secrets masked, "
        f"{_LIVE_PATH} observed unmocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
