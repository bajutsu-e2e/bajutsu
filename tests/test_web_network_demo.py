"""The web network demo (BE-0282) — fast, browser-free coverage of the pieces the CI lane wires.

The real interception/capture/redaction path is exercised by the `network (playwright)` CI job
(`make -C demos/web e2e-network`) against a real Chromium; that browser run is out of the `make
check` gate. These tests cover what *can* be checked deterministically on Linux: that the demo
scenario is well-formed and mocks/asserts the endpoint the demo app calls, and that the demo's
`redact` policy actually masks the secret shape that app sends — so a policy/endpoint drift breaks
the fast gate, not only the browser lane. They also run the lane's own redaction check
(`demos/web/network/assert_redaction.py`) over synthetic evidence, since the browser lane only ever
hands it evidence that is already masked and so cannot show the check still rejects anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from bajutsu.common.evidence.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models.evidence import Redact
from demos.web.network.assert_redaction import (
    _BODY_SECRET,
    _HEADER_SECRET,
    _KEPT_FIELD,
    _KEPT_VALUE,
    main,
)

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


def test_the_kept_field_and_value_are_what_the_app_actually_sends() -> None:
    # The kept field guards against over-redaction only while the app really sends it: renaming the
    # Sync body's non-secret key, or changing its value, would otherwise leave the check looking for
    # something nothing sends, reddening only the browser lane. The app builds the body with
    # `JSON.stringify` over an object literal, so the key is bare in source and quoted only once
    # serialized — the form the checker matches — hence the unquoting here. The value is a quoted
    # string literal in both places, so it is matched as-is.
    source = (_DEMO / "app" / "index.html").read_text(encoding="utf-8")
    key = _KEPT_FIELD.strip('"')
    assert f"{key}:" in source
    assert _KEPT_VALUE in source


# --- The checker itself, executed (BE-0282) -------------------------------------------------------
# `assert_redaction.py` is the sole observer of BE-0282's sharpest claim — that a *really captured*
# secret is masked in the persisted evidence — and its only other executor is the metered
# `network (playwright)` job, which only ever feeds it evidence a working redactor produced. So
# nothing else stops it from quietly becoming a check that passes on bad evidence: relax a comparison
# and CI stays green, because real evidence happens to be masked. These run `main` over synthetic
# network.json files instead, one mutation per case, each pinned to the message its own rule prints —
# an exit code alone would stay green if one rule started swallowing another's mutation, which is the
# same silent-pass this suite exists to catch.

# The shape a real browser run writes for the mocked Sync request, already redacted.
_SYNC_EXCHANGE: dict[str, Any] = {
    "method": "POST",
    "url": "http://127.0.0.1:8787/api/sync",
    "path": _ENDPOINT,
    "status": 201,
    "requestHeaders": {"content-type": "application/json", "authorization": PLACEHOLDER},
    "requestBody": f'{{"account":"a@b.com","password":"{PLACEHOLDER}"}}',
    "mocked": True,
}


def _write_run(runs_dir: Path, *exchanges: dict[str, Any]) -> Path:
    """Lay out one run's evidence the way `bajutsu run` does: a `<sid>/network.json` per scenario."""
    for index, exchange in enumerate(exchanges):
        scenario_dir = runs_dir / "20260101-000000" / f"{index:02d}-network"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "network.json").write_text(json.dumps([exchange]), encoding="utf-8")
    return runs_dir


def test_the_redaction_check_passes_a_well_formed_run(tmp_path: Path) -> None:
    runs = _write_run(tmp_path, dict(_SYNC_EXCHANGE))
    assert main(["assert_redaction.py", str(runs)]) == 0


def _drop_key(key: str) -> Callable[[dict[str, Any]], None]:
    def mutate(exchange: dict[str, Any]) -> None:
        exchange.pop(key, None)

    return mutate


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(exchange: dict[str, Any]) -> None:
        exchange[key] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            _set("mocked", False),
            "is not marked mocked",
            id="stub-served-exchange-not-marked-mocked",
        ),
        pytest.param(
            _set("mocked", "true"), "is not marked mocked", id="mocked-is-a-string-not-a-bool"
        ),
        pytest.param(
            _drop_key("mocked"), "is not marked mocked", id="mocked-absent-rather-than-recorded"
        ),
        pytest.param(
            _set("status", 200),
            "expected the mock's 201 status",
            id="a-live-server-answered-instead-of-the-mock",
        ),
        pytest.param(_drop_key("status"), "expected the mock's 201 status", id="status-absent"),
        pytest.param(
            _set("requestHeaders", {"authorization": f"Bearer {_HEADER_SECRET}"}),
            "Authorization header not masked",
            id="authorization-header-unmasked",
        ),
        pytest.param(
            _set("requestHeaders", {}),
            "Authorization header not masked",
            id="authorization-header-absent",
        ),
        pytest.param(
            _set("requestBody", f'{{"account":"a@b.com","password":"{_BODY_SECRET}"}}'),
            "leaked its value into network.json",
            id="password-body-field-unmasked",
        ),
        pytest.param(
            _drop_key("requestBody"),
            "the password body field was not masked",
            id="request-body-absent",
        ),
        # The secret is gone and the non-secret field survived, yet the field was *dropped* rather
        # than masked — so the evidence no longer records that a credential was ever sent. Only the
        # "placeholder present" rule separates masking from deletion.
        pytest.param(
            _set("requestBody", '{"account":"a@b.com"}'),
            "the password body field was not masked",
            id="password-field-dropped",
        ),
        # Over-redaction: both "secret gone" and "placeholder present" hold, yet the evidence is
        # useless. Only the surviving-key check separates this from a correct masking.
        pytest.param(
            _set("requestBody", PLACEHOLDER),
            "did not survive redaction",
            id="whole-body-replaced-by-the-placeholder",
        ),
        # The adjacent shape the key check alone would pass: every key survives, every *value* is
        # masked, so the evidence records the request's shape and nothing about what it carried.
        pytest.param(
            _set("requestBody", f'{{"account":"{PLACEHOLDER}","password":"{PLACEHOLDER}"}}'),
            "value was masked along with the secret",
            id="every-value-masked-including-the-non-secret-one",
        ),
    ],
)
def test_the_redaction_check_fails_loudly_on_a_bad_exchange(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exchange = dict(_SYNC_EXCHANGE)
    mutate(exchange)
    runs = _write_run(tmp_path, exchange)
    with pytest.raises(SystemExit) as exc:
        main(["assert_redaction.py", str(runs)])
    assert exc.value.code == 1
    # Pinned to the rule's own message, not merely a non-zero exit: were an earlier rule to start
    # catching this mutation, the case would stay green while the rule it targets went quiet.
    assert message in capsys.readouterr().err


def test_the_redaction_check_fails_when_a_run_captured_nothing(tmp_path: Path) -> None:
    # The vacuous pass the whole lane would otherwise be worth nothing against: network capture off,
    # an interception that never fired, or a run that died before writing evidence.
    (tmp_path / "20260101-000000").mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["assert_redaction.py", str(tmp_path)])
    assert exc.value.code == 1


def test_the_redaction_check_fails_on_a_second_matching_exchange(tmp_path: Path) -> None:
    # `_load_sync_exchange`'s count rule: a retried scenario or a newly added one writing a second
    # /api/sync exchange must fail rather than let a first-match short-circuit pick the checked one.
    runs = _write_run(tmp_path, dict(_SYNC_EXCHANGE), dict(_SYNC_EXCHANGE))
    with pytest.raises(SystemExit) as exc:
        main(["assert_redaction.py", str(runs)])
    assert exc.value.code == 1


def test_the_redaction_check_sweeps_for_a_secret_outside_the_sync_exchange(tmp_path: Path) -> None:
    # The belt-and-braces sweep over the raw file text, the one rule the per-exchange checks don't
    # reach: another exchange the same scenario captured may not carry the secret either.
    runs = tmp_path / "20260101-000000" / "00-network"
    runs.mkdir(parents=True)
    (runs / "network.json").write_text(
        json.dumps(
            [
                dict(_SYNC_EXCHANGE),
                {"method": "POST", "path": "/other", "requestBody": _BODY_SECRET},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(["assert_redaction.py", str(tmp_path)])
    assert exc.value.code == 1
