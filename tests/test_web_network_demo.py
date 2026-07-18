"""The web network demo (BE-0282) — fast, browser-free coverage of the pieces the CI lane wires.

The real interception/capture/redaction path is exercised by the `network (playwright)` CI job
(`make -C demos/web e2e-network`) against a real Chromium; that browser run is out of the `make
check` gate. These tests cover what *can* be checked deterministically on Linux: that the demo
scenario is well-formed and mocks/asserts the endpoint the demo app calls, and that the demo's
`redact` policy actually masks the secret shape that app sends — so a policy/endpoint drift breaks
the fast gate, not only the browser lane.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bajutsu.evidence.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models.evidence import Redact
from demos.web.network.assert_redaction import _BODY_SECRET, _HEADER_SECRET

_REPO = Path(__file__).resolve().parent.parent
_DEMO = _REPO / "demos" / "web"

# The demo's secret shape has one Python source of truth — the assert_redaction script that also
# checks the real captured evidence; the app (demos/web/app/index.html) sends the same values (the
# header token behind `Bearer`), verified end to end by the browser lane.
_ENDPOINT = "/api/sync"


def _web_redact() -> Redact:
    cfg = yaml.safe_load((_DEMO / "demo.config.yaml").read_text(encoding="utf-8"))
    return Redact.model_validate(cfg["targets"]["web"].get("redact", {}))


def test_network_scenario_mocks_and_asserts_the_app_endpoint() -> None:
    text = (_DEMO / "scenarios" / "network.yaml").read_text(encoding="utf-8")
    scenarios = load_scenario_file(text).scenarios
    assert len(scenarios) == 1
    sc = scenarios[0]

    # Tagged so the default `make -C demos/web e2e` (--no-network) excludes it — under --no-network
    # mocks are not served and nothing is captured, which this scenario needs.
    assert "network" in sc.tags

    # Mocks the exact endpoint the app fetches, with a distinct status so a captured 201 proves the
    # mock — not a live server — served it.
    assert len(sc.mocks) == 1
    mock = sc.mocks[0]
    assert mock.match.method == "POST"
    assert mock.match.path_matches == r"/api/sync$"
    assert mock.respond.status == 201

    # Asserts the captured request deterministically (the interception/capture check).
    request_expects = [a for a in sc.expect if a.request is not None]
    assert len(request_expects) == 1
    req = request_expects[0].request
    assert req is not None
    assert req.method == "POST"
    assert req.path_matches == r"/api/sync$"
    assert req.status == 201


def test_demo_redact_policy_masks_the_sync_secret() -> None:
    redactor = Redactor(_web_redact())
    exchange = redactor.redact_exchange(
        {
            "method": "POST",
            "path": _ENDPOINT,
            "requestHeaders": {"Authorization": f"Bearer {_HEADER_SECRET}", "Accept": "*/*"},
            "requestBody": f'{{"account":"a@b.com","password":"{_BODY_SECRET}"}}',
        }
    )
    # Authorization masked by name (BE-0130 default); a non-secret header stays legible.
    assert exchange["requestHeaders"]["Authorization"] == PLACEHOLDER
    assert exchange["requestHeaders"]["Accept"] == "*/*"
    # The password body field is scrubbed by the demo's `fields: [password]` policy.
    assert _BODY_SECRET not in exchange["requestBody"]
    assert PLACEHOLDER in exchange["requestBody"]
    assert "a@b.com" in exchange["requestBody"]  # non-secret field kept
