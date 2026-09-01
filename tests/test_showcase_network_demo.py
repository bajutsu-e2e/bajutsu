"""The showcase network lane (BE-0282) — fast, Simulator-free coverage of what the CI job wires.

The real interception / capture / redaction path is exercised by the `network (xcuitest)` CI job
(`make -C demos/showcase e2e-network`) against a booted Simulator; that run is out of the `make
check` gate. These tests cover what *can* be checked deterministically on any host: that the two
scenarios the lane runs are well-formed and mock / assert the endpoints the app actually calls, that
the showcase's `redact` policy masks the secret shape the app sends, and that the literal secrets the
redaction check compares against are still the ones the app's source sends — so a policy, endpoint,
or secret drift breaks the fast gate rather than only the metered macOS lane.

The web twin is `tests/test_web_network_demo.py`; this file is its iOS counterpart, over the
BajutsuKit → loopback POST → collector transport instead of the browser's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from bajutsu.evidence.network import NetworkExchange
from bajutsu.evidence.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import load_scenario_file
from bajutsu.scenario.models import Gone, Scenario, WaitRequest
from bajutsu.scenario.models.evidence import Redact
from demos.showcase.network.assert_network_evidence import (
    _BODY_SECRET,
    _HEADER_SECRET,
    _KEPT_FIELD,
    _KEPT_NUMBER_FIELD,
    _KEPT_PAIR,
    _LIVE_PATH,
    _MOCK_PATH,
    main,
)

_REPO = Path(__file__).resolve().parent.parent
_SHOWCASE = _REPO / "demos" / "showcase"
_SCENARIOS = _SHOWCASE / "scenarios"
_NOAX_SCENARIOS = _SHOWCASE / "ios" / "scenarios-noax"
_IOS_WORKFLOW = _REPO / ".github" / "workflows" / "ios-e2e.yml"

# The `network` job's artifact — the handle the upload-path pin below identifies its step by.
_NET_ARTIFACT = "ios-e2e-network-run"

# `NET_RUNS ?= $(ROOT)/tmp/showcase-network-runs` — the throwaway run directory the lane's
# `--runs-dir` writes to, read as a path relative to the repository root (`ROOT`).
_NET_RUNS_RE = re.compile(r"^NET_RUNS\s*\?=\s*\$\(ROOT\)/(\S+)\s*$", re.MULTILINE)

# The target the lane drives, and so the one whose `redact` policy applies to its evidence.
_TARGET = "showcase-swiftui"


def _showcase_redact() -> Redact:
    cfg = yaml.safe_load((_SHOWCASE / "showcase.config.yaml").read_text(encoding="utf-8"))
    return Redact.model_validate(cfg["targets"][_TARGET].get("redact", {}))


def _log_view_source() -> str:
    """The SwiftUI Log screen's source — the one place the lane's secrets and body keys come from."""
    return (_SHOWCASE / "ios" / "swiftui" / "Sources" / "LogView.swift").read_text(encoding="utf-8")


def _sole_scenario(name: str, directory: Path = _SCENARIOS) -> Scenario:
    scenarios = load_scenario_file((directory / name).read_text(encoding="utf-8")).scenarios
    assert len(scenarios) == 1
    return scenarios[0]


def test_mock_scenario_stubs_and_asserts_the_log_submit() -> None:
    sc = _sole_scenario("network_mock.yaml")
    assert "network" in sc.tags

    # Stubs the exact endpoint the Log submit POSTs to, with a status no live server would answer
    # (httpbin returns 200), so a captured 201 proves the in-app stub — not the network — served it.
    assert len(sc.mocks) == 1
    mock = sc.mocks[0]
    assert mock.match.method == "POST"
    assert mock.match.path_matches == r"/post$"
    assert mock.respond.status == 201

    request_expects = [a for a in sc.expect if a.request is not None]
    assert len(request_expects) == 1
    req = request_expects[0].request
    assert req is not None
    assert req.method == "POST"
    assert req.path == _MOCK_PATH
    assert req.status == 201


# The mock scenario watches a toast the app dismisses a fixed delay after the response lands, and
# every step boundary in between spends part of that delay on the run loop's own evidence capture —
# the `screenshot.before` baseline, a tree read, and the previous step's artifact writes, together
# 0.2-1.2s per boundary on a CI Simulator. So the lane is deterministic only while both halves of
# this fixture contract hold: the toast wait is armed on the step *right after* the tap, and the
# toast outlives one boundary with room to spare. Both were violated at once until #1744 — an
# `until: request` wait sat between the tap and the toast wait, and the toast lived 1.2s — which cost
# the `network (xcuitest)` job roughly one run in two, always on the same `wait timeout: for {'id':
# 'log.toast'}`. Neither half is observable off-device, so this is where a re-ordering edit or a
# shortened toast has to fail.
_TOAST_DISMISS_FLOOR_MS = 2500


def _assert_toast_waits_follow_the_submit_tap(sc: Scenario) -> None:
    taps = [i for i, step in enumerate(sc.steps) if step.tap is not None]
    assert len(taps) == 2, "the Log tab, then Submit"
    submit = taps[-1]

    appeared = sc.steps[submit + 1].wait
    assert appeared is not None and appeared.for_ is not None
    assert "toast" in str(appeared.for_) or appeared.for_.label == "Saved"

    cleared = sc.steps[submit + 2].wait
    assert cleared is not None and isinstance(cleared.until, Gone)

    # The request wait sits after the pair, where it costs the toast nothing: the toast only appears
    # once the response has landed, so by the time it runs it is already satisfied.
    observed = sc.steps[submit + 3].wait
    assert observed is not None and isinstance(observed.until, WaitRequest)


def test_the_toast_waits_are_armed_on_the_step_right_after_the_submit_tap() -> None:
    _assert_toast_waits_follow_the_submit_tap(_sole_scenario("network_mock.yaml"))
    # The -noax twin drives the same screen by label and is kept in step with it deliberately.
    _assert_toast_waits_follow_the_submit_tap(_sole_scenario("network_mock.yaml", _NOAX_SCENARIOS))


def test_the_app_dismisses_the_toast_slower_than_a_step_boundary() -> None:
    # Anchored on the `showToast = false` it guards, so an unrelated `Task.sleep` added earlier in
    # the file cannot quietly become the pin's subject.
    swiftui = re.search(
        r"Task\.sleep\(for: \.milliseconds\((\d+)\)\)\n\s*showToast = false", _log_view_source()
    )
    assert swiftui is not None, "the toast's auto-dismiss delay moved out of LogView.swift"
    assert int(swiftui.group(1)) >= _TOAST_DISMISS_FLOOR_MS

    # The UIKit twin runs both scenarios too (`run-uikit`, `run-uikit-noax`), so its own toast has
    # to outlive a boundary as well.
    uikit = (_SHOWCASE / "ios" / "uikit" / "Sources" / "LogController.swift").read_text(
        encoding="utf-8"
    )
    dismiss = re.search(r"asyncAfter\(deadline: \.now\(\) \+ ([\d.]+)\)", uikit)
    assert dismiss is not None, "the toast's auto-dismiss delay moved out of LogController.swift"
    assert float(dismiss.group(1)) * 1000 >= _TOAST_DISMISS_FLOOR_MS


def test_live_scenario_asserts_only_that_the_catalog_request_was_observed() -> None:
    sc = _sole_scenario("network_live.yaml")
    assert "network" in sc.tags

    # No mock: this is the unstubbed half, which is what makes the pair able to prove `mocked` is
    # provenance the collector recorded rather than a default (assert_network_evidence.py checks that).
    assert sc.mocks == []

    request_expects = [a for a in sc.expect if a.request is not None]
    assert len(request_expects) == 1
    req = request_expects[0].request
    assert req is not None
    assert req.method == "GET"
    # Pinned against the checker's own constant, so the two cannot drift apart.
    assert req.path_matches == _LIVE_PATH + "$"
    # Deliberately no status: the upstream response varies (and can fail outright on a host with no
    # egress), so the scenario asserts only the deterministic fact that the exchange was observed.
    assert req.status is None


def test_showcase_redact_policy_masks_the_log_submit_secrets() -> None:
    redactor = Redactor(_showcase_redact())
    exchange = redactor.redact_exchange(
        {
            "method": "POST",
            "path": _MOCK_PATH,
            "requestHeaders": {
                "Authorization": f"Bearer {_HEADER_SECRET}",
                "Content-Type": "application/json",
            },
            "requestBody": f'{{"note":"n","count":1,"password":"{_BODY_SECRET}"}}',
        }
    )
    # Authorization masked by name (BE-0130 default); a non-secret header stays legible.
    assert exchange["requestHeaders"]["Authorization"] == PLACEHOLDER
    assert exchange["requestHeaders"]["Content-Type"] == "application/json"
    # The password body field is scrubbed by the target's `fields: [password]` policy.
    assert _BODY_SECRET not in exchange["requestBody"]
    assert PLACEHOLDER in exchange["requestBody"]
    assert '"note":"n"' in exchange["requestBody"]  # non-secret field kept


def test_every_redacted_field_is_a_key_the_app_actually_sends() -> None:
    # The other half of the drift guard below: that one pins the secret *values*, this one pins the
    # body *key* the `redact` policy names. Renaming the app's JSON key would otherwise leave both the
    # policy and the hand-built exchange above agreeing on a name nothing sends, shipping the secret
    # unmasked with only the metered macOS lane going red. Same shape as
    # `tests/test_showcase_fixtures.py`'s app-source literal pins, for the same reason.
    source = _log_view_source()
    for field in _showcase_redact().fields:
        assert f'"{field}"' in source, field
    # And the two non-secret parts the checker requires to survive redaction, so over-redaction stays
    # detectable (both constants are already quoted). The pair's *value* is pinned separately: it is
    # the stepper's `@State` default, which the lane's scenarios never tap, so a source edit raising
    # the default is what would silently turn the value rule into a check on a string nothing sends.
    assert _KEPT_FIELD in source
    assert f'"{_KEPT_NUMBER_FIELD}"' in source
    assert f"var {_KEPT_NUMBER_FIELD} = {_KEPT_PAIR.rpartition(':')[2]}" in source
    # The value half's other precondition: the pair holds only while nothing moves the stepper. Any
    # mention of its id in the lane's own scenarios fails here — read or write, since a scenario that
    # needs one is the moment to re-derive the pinned value rather than let the metered macOS lane
    # report a scenario edit as a redaction failure.
    for name in ("network_mock.yaml", "network_live.yaml"):
        assert "log.count" not in (_SCENARIOS / name).read_text(encoding="utf-8"), name


def test_an_unmocked_exchange_is_persisted_with_mocked_explicitly_false(tmp_path: Path) -> None:
    # `assert_network_evidence.py` requires `mocked is False`, not merely falsy, because an absent key
    # would make the provenance check vacuous — and the app never sends the key for an unstubbed
    # request, so the persisted `false` is the model's own default surviving the dump. Every other
    # reader in the repo tolerates its absence (`bajutsu/report/panels.py`, `bajutsu/trace.py` both use
    # a truthy test), so nothing else would notice the field becoming optional or being elided. Pinned
    # through the run pipeline's own writer rather than a replicated `model_dump` call, so a change to
    # *those* kwargs (an `exclude_defaults=True` added to shrink evidence, say) fails here instead of
    # only reddening the non-gating macOS lane. Local imports match `tests/runner/test_pipeline.py`'s
    # own `_write_network` tests.
    from bajutsu.common.runner.pipeline import _write_network
    from bajutsu.evidence.sink import RunArtifactWriter

    exchange = NetworkExchange(
        method="GET", url="https://example.com/horses", path=_LIVE_PATH, status=404
    )
    art = _write_network(
        [(exchange, 1.0)],
        RunArtifactWriter(tmp_path, Redactor(None)),
        "00-live",
        wall_offset_s=0.0,
    )
    assert art is not None
    persisted = json.loads((tmp_path / "00-live" / "network.json").read_text(encoding="utf-8"))
    assert "mocked" in persisted[0]
    assert persisted[0]["mocked"] is False


def test_the_checked_secrets_are_the_ones_the_app_sends() -> None:
    # The redaction check compares against literals; the app is where they come from. Pin them to the
    # source so renaming the demo secret fails here rather than silently turning assert_network_evidence.py
    # into a check that passes because it is looking for a string nothing sends any more.
    source = _log_view_source()
    assert _HEADER_SECRET in source
    assert _BODY_SECRET in source


# --- The checker itself, executed (BE-0282) -------------------------------------------------------
# `assert_network_evidence.py` is the sole observer of BE-0282's sharpest claim — that a *really
# captured* secret is masked in the persisted evidence — and its only other executor is the metered,
# deliberately non-gating `network (xcuitest)` job. So nothing but the tests below stops it from
# quietly becoming a check that passes on bad evidence: relax a comparison and CI stays green,
# because real evidence happens to be masked. These run `main` over synthetic evidence instead, one
# mutation per case, so each rule fails loudly for its own reason.

# The shape a real run writes, taken from a verified Simulator run: two scenarios, so two files.
_MOCK_EXCHANGE: dict[str, Any] = {
    "method": "POST",
    "url": "https://httpbin.org/post",
    "path": _MOCK_PATH,
    "status": 201,
    "requestHeaders": {"Content-Type": "application/json", "Authorization": PLACEHOLDER},
    "requestBody": f'{{"intense":false,"count":1,"note":"","password":"{PLACEHOLDER}"}}',
    "mocked": True,
}
_LIVE_EXCHANGE: dict[str, Any] = {
    "method": "GET",
    "url": "https://example.com/horses",
    "path": _LIVE_PATH,
    "status": 404,
    "requestHeaders": {},
    "mocked": False,
}


def _write_run(runs_dir: Path, mock: dict[str, Any] | None, live: dict[str, Any] | None) -> Path:
    """Lay out one run's evidence the way `bajutsu run` does: one `<sid>/network.json` per scenario."""
    for sid, exchange in (("00-mock", mock), ("01-live", live)):
        if exchange is None:
            continue
        scenario_dir = runs_dir / "20260101-000000" / sid
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "network.json").write_text(json.dumps([exchange]), encoding="utf-8")
    return runs_dir


def test_the_evidence_check_passes_a_well_formed_run(tmp_path: Path) -> None:
    runs = _write_run(tmp_path, dict(_MOCK_EXCHANGE), dict(_LIVE_EXCHANGE))
    assert main(["assert_network_evidence.py", str(runs)]) == 0


def _drop_key(key: str) -> Callable[[dict[str, Any]], None]:
    def mutate(exchange: dict[str, Any]) -> None:
        exchange.pop(key, None)

    return mutate


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(exchange: dict[str, Any]) -> None:
        exchange[key] = value

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_set("mocked", False), id="stub-served-exchange-not-marked-mocked"),
        pytest.param(_set("mocked", "true"), id="mocked-is-a-string-not-a-bool"),
        pytest.param(_drop_key("mocked"), id="mocked-absent-rather-than-recorded"),
        pytest.param(_set("status", 200), id="a-live-server-answered-instead-of-the-stub"),
        pytest.param(_drop_key("status"), id="status-absent"),
        pytest.param(
            _set("requestHeaders", {"Authorization": f"Bearer {_HEADER_SECRET}"}),
            id="authorization-header-unmasked",
        ),
        pytest.param(_set("requestHeaders", {}), id="authorization-header-absent"),
        pytest.param(
            _set("requestBody", f'{{"note":"","password":"{_BODY_SECRET}"}}'),
            id="password-body-field-unmasked",
        ),
        pytest.param(_drop_key("requestBody"), id="request-body-absent"),
        # The secret is gone and the non-secret field survived, yet the field was *dropped* rather
        # than masked — so the evidence no longer records that a credential was ever sent. Only the
        # "placeholder present" rule separates masking from deletion.
        pytest.param(_set("requestBody", '{"note":"","count":1}'), id="password-field-dropped"),
        # The over-redaction shapes, one case each so neither kept-part rule ships untested.
        # A non-secret key gone while the rest of the body is intact — only the kept-*key* rule
        # reaches this one; the kept-value rule below is satisfied.
        pytest.param(
            _set("requestBody", f'{{{_KEPT_PAIR},"password":"{PLACEHOLDER}"}}'),
            id="a-non-secret-field-dropped-from-the-body",
        ),
        # The body replaced wholesale: "secret gone" and "placeholder present" both hold, yet the
        # evidence is useless. Caught first by the kept-key rule.
        pytest.param(_set("requestBody", PLACEHOLDER), id="whole-body-replaced-by-the-placeholder"),
        # Every value masked with every key intact — the shape `Redactor` can actually produce, since
        # `_masked` rewrites a matched field in place and never drops a key. A widened field pattern
        # gets here, and the kept-key rule passes it; only the kept-*value* rule reaches it.
        pytest.param(
            _set(
                "requestBody",
                json.dumps(
                    dict.fromkeys(("intense", "count", "note", "password"), PLACEHOLDER),
                    separators=(",", ":"),
                ),
            ),
            id="every-value-masked-with-the-keys-intact",
        ),
    ],
)
def test_the_evidence_check_fails_loudly_on_a_bad_mocked_exchange(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    mock = dict(_MOCK_EXCHANGE)
    mutate(mock)
    runs = _write_run(tmp_path, mock, dict(_LIVE_EXCHANGE))
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(runs)])
    assert exc.value.code == 1


@pytest.mark.parametrize(
    "mutate",
    [
        # `mocked` is provenance: an unstubbed request claiming to have been mocked is the exact
        # "default that happens to read true" this half of the check exists to rule out.
        pytest.param(_set("mocked", True), id="unstubbed-exchange-claims-it-was-mocked"),
        pytest.param(_drop_key("mocked"), id="mocked-absent-rather-than-recorded-false"),
        pytest.param(_set("method", "POST"), id="not-the-catalog-get"),
        pytest.param(_drop_key("method"), id="method-absent"),
    ],
)
def test_the_evidence_check_fails_loudly_on_a_bad_observed_exchange(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    live = dict(_LIVE_EXCHANGE)
    mutate(live)
    runs = _write_run(tmp_path, dict(_MOCK_EXCHANGE), live)
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(runs)])
    assert exc.value.code == 1


def test_the_evidence_check_fails_when_a_run_captured_nothing(tmp_path: Path) -> None:
    # The vacuous pass the whole lane would otherwise be worth nothing against: network capture off,
    # a collector that never received anything, or a run that died before writing evidence.
    (tmp_path / "20260101-000000").mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(tmp_path)])
    assert exc.value.code == 1


@pytest.mark.parametrize("missing", ["mock", "live"])
def test_the_evidence_check_fails_when_either_exchange_is_missing(
    tmp_path: Path, missing: str
) -> None:
    # Neither leg may be silently absent: the mocked one carries the redaction claim, the observed one
    # the provenance claim, and a run that wrote only one proves only half of what the lane asserts.
    runs = _write_run(
        tmp_path,
        None if missing == "mock" else dict(_MOCK_EXCHANGE),
        None if missing == "live" else dict(_LIVE_EXCHANGE),
    )
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(runs)])
    assert exc.value.code == 1


def test_the_evidence_check_fails_on_a_second_matching_exchange(tmp_path: Path) -> None:
    # `_sole`'s count rule: a retried scenario or a newly added one writing a second /post exchange
    # must fail rather than let a first-match short-circuit decide which one was checked.
    runs = _write_run(tmp_path, dict(_MOCK_EXCHANGE), dict(_LIVE_EXCHANGE))
    extra = runs / "20260101-000000" / "02-mock-again"
    extra.mkdir()
    (extra / "network.json").write_text(json.dumps([dict(_MOCK_EXCHANGE)]), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(runs)])
    assert exc.value.code == 1


def test_the_evidence_check_sweeps_for_a_secret_outside_the_two_exchanges(tmp_path: Path) -> None:
    # The belt-and-braces sweep over the raw file text, the one rule no per-exchange check reaches: a
    # third exchange the run happened to capture may not carry the secret either.
    runs = _write_run(tmp_path, dict(_MOCK_EXCHANGE), dict(_LIVE_EXCHANGE))
    leaky = runs / "20260101-000000" / "02-elsewhere"
    leaky.mkdir()
    (leaky / "network.json").write_text(
        json.dumps([{"method": "POST", "path": "/other", "requestBody": _BODY_SECRET}]),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(["assert_network_evidence.py", str(runs)])
    assert exc.value.code == 1


# --- the Makefile ↔ workflow coupling the lane's artifact depends on -------------------------------


def _network_job_steps() -> list[dict[str, Any]]:
    """Parse rather than scan: an indentation scanner can read a neighbouring step's block instead."""
    workflow = yaml.safe_load(_IOS_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["network"]["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _network_artifact_paths(steps: list[dict[str, Any]]) -> list[str]:
    """Keyed on the artifact's own name, so a reordered or newly added upload step cannot shadow it."""
    uploads = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
        and step.get("with", {}).get("name") == _NET_ARTIFACT
    ]
    assert len(uploads) == 1, (
        f"the `network` job has no unique step uploading the {_NET_ARTIFACT!r} artifact — this pin "
        "identifies the upload by that name."
    )
    path = uploads[0]["with"]["path"]
    entries = path.split() if isinstance(path, str) else list(path)
    return [str(entry).strip().strip("\"'").rstrip("/") for entry in entries]


def test_the_network_jobs_artifact_uploads_the_makefile_runs_dir() -> None:
    # The one Makefile↔workflow coupling the lane introduces that no other pin covers: `NET_RUNS` is
    # duplicated as a bare literal in the `network` job's upload step, which runs with
    # `if-no-files-found: ignore`, so a rename on either side would empty the artifact with a green
    # step — and on a deliberately non-gating job that artifact is the whole debugging surface. Pin
    # the two together in the same shape as tests/test_e2e_changes.py's target-name and guard pins.
    makefile = (_SHOWCASE / "Makefile").read_text(encoding="utf-8")
    m = _NET_RUNS_RE.search(makefile)
    assert m is not None, (
        "demos/showcase/Makefile no longer declares `NET_RUNS ?= $(ROOT)/<path>` — this pin reads "
        "that line to learn the run directory the workflow must upload."
    )
    net_runs = m.group(1).rstrip("/")
    steps = _network_job_steps()
    paths = _network_artifact_paths(steps)
    assert net_runs in paths, (
        f"demos/showcase/Makefile writes the network lane's run directory to {net_runs!r}, but the "
        f"`network` job's artifact uploads {paths!r} — move both together, or the artifact ships "
        "empty of the captured evidence with a green step."
    )
    # `NET_RUNS` is a `?=` default, so a `NET_RUNS=…` on the job's own `make` line would move the real
    # directory while leaving both pinned lines — and this test — untouched.
    runs = [str(step.get("run", "")) for step in steps if "e2e-network" in str(step.get("run", ""))]
    assert runs and not any("NET_RUNS" in run for run in runs), (
        f"the `network` job's e2e-network step overrides NET_RUNS ({runs!r}); the upload path above "
        "is pinned against the Makefile default, which the override would bypass."
    )
