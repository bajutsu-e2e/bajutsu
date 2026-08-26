"""The serve UI's fetch layer tells a failed read apart from an empty one (issue #1716).

`getJSON` (serve.core.mjs) used to resolve to the caller's fallback on *any* thrown error and never
looked at `res.ok`, so a non-2xx JSON error body reached the caller as data: a list-expecting picker
got an `{error}` object to `.map` over, and a panel that renders "nothing here yet" reported a broken
request as an empty result. These are structural tests in the style of the other serve UI suites —
the modules ship without a JS test harness (see `eslint.config.mjs`), so we pin the guarantees in the
shipped source, plus the server-side premise the bug rested on.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from _shared import _get, _serve, project

from bajutsu import serve as srv


def _fetch(tmp_path: Path, route: str) -> str:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return _get(port, route)[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def _fn_body(module: str, signature: str) -> str:
    """The *code* of one top-level function, from its signature to the first column-0 `}`.

    Whole-line `//` comments are dropped: every guarantee below names the thing it pins, so a body
    that kept its prose would let the comment explaining a call satisfy the assertion about the call
    — and the check would survive the code being reverted underneath it.
    """
    start = module.index(signature)
    end = module.index("\n}", start)
    lines = module[start:end].splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("//"))


def test_a_failing_api_get_answers_with_parseable_json(tmp_path: Path) -> None:
    """The premise behind #1716: `handler._json` answers a 4xx/5xx with a JSON `{error}` body, so
    `res.json()` resolves rather than throwing — which is why a try/catch alone could not see the
    failure and `res.ok` has to be checked."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope")
    finally:
        server.shutdown()
        server.server_close()
    assert ei.value.code == 404
    assert ei.value.headers.get("Content-Type") == "application/json"
    # It parses, so a bare try/catch sees a resolved value indistinguishable from a real payload.
    assert json.loads(ei.value.read())["error"]


def test_get_json_rejects_a_non_2xx_body(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.core.mjs")
    body = _fn_body(text, "async function getJSON(")
    assert "res.ok" in body  # a non-2xx never resolves to the response body…
    assert "failedJSON(fallback,data)" in body  # …the failure value is substituted for it instead


def test_post_json_still_hands_back_a_write_s_error_body(tmp_path: Path) -> None:
    """`postJSON` deliberately keeps the old contract: its callers render the server's message from a
    non-2xx body (a purge's 403 becomes "Only an admin can permanently delete a run"), so gating it
    on `res.ok` would replace every specific reason with a generic one."""
    text = _fetch(tmp_path, "/serve.core.mjs")
    # `.ok`, not `res.ok`: postJSON binds no `res`, so a future `if(!r.ok)return fallback` would
    # slip past the narrower spelling while breaking exactly the contract this pins.
    assert ".ok" not in _fn_body(text, "async function postJSON(")


def test_the_failure_sentinel_ships_and_is_exported(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.core.mjs")
    assert "Object.freeze(" in text  # identity-compared, so no payload can match it
    assert "function isFetchError(" in text
    # Importable by the section modules, wherever the export list happens to order it.
    exports = text[text.index("\nexport {") : text.index("};", text.index("\nexport {"))]
    assert "FETCH_ERROR" in exports
    assert "isFetchError" in exports


def test_metrics_separates_a_failed_read_from_an_empty_hub(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    assert 'data-testid="metrics.error"' in text
    assert 'data-testid="metrics.empty"' in text  # the genuine "no projects registered" state
    body = _fn_body(text, "async function loadMetrics(")
    assert "/api/metrics/projects" in body
    assert (
        "FETCH_ERROR" in body
    )  # the call itself asks for one, so a failed read can't read as empty


def test_history_separates_a_failed_read_from_an_empty_history(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.panels.mjs")
    assert 'data-testid="replay.history-error"' in text
    assert (
        "'no runs yet'" in text
    )  # still the copy for a genuinely empty history, not for a failure
    # The bare assignment, distinct from the success path's `'History'+(runs.length?…)`: a failed
    # read drops the tab's stale "(N)" rather than advertising a count nothing on screen backs.
    assert "tab.textContent='History';" in _fn_body(text, "async function loadHistory(")


def test_crawl_history_separates_a_failed_read_from_an_empty_history(tmp_path: Path) -> None:
    """The Crawl history is the same surface class as the Replay one — same `wireHistoryList` wiring,
    same placeholder — so it separates the two states the same way, rather than leaving the sibling
    list contradicting the rule."""
    text = _fetch(tmp_path, "/serve.crawl.mjs")
    assert 'data-testid="crawl.history-error"' in text
    assert ">no crawls yet<" in text  # still the copy for a genuinely empty history
    assert "tab.textContent='History';" in _fn_body(text, "async function loadCrawlHistory(")


def test_coverage_run_picker_reports_a_failed_read(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.panels.mjs")
    assert 'data-testid="coverage.runs-error"' in text
