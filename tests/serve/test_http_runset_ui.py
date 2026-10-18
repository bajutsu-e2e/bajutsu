"""Structural tests for the Replay tab's device-cloud form and run-set panel (BE-0465 units 2-3).

The markup ships inlined in the index; the JS ships as the serve.runset.mjs ES module (BE-0247).
The dispatch, environment listing and job read are covered by their operation tests; here we pin
that the form's controls exist, which local-only controls it hides, and that the module talks to the
endpoints the design names — run-set with explicit names, and a status poll without the log.
"""

from __future__ import annotations

import re
from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _fetch(tmp_path: Path, *routes: str) -> list[str]:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return [_get(port, route)[1].decode("utf-8") for route in routes]
    finally:
        server.shutdown()
        server.server_close()


def _tag(html: str, testid: str) -> str:
    """The opening tag carrying *testid*, so a test can read that one element's classes."""
    match = re.search(rf'<[^>]*data-testid="{re.escape(testid)}"[^>]*>', html)
    assert match is not None, testid
    return match.group(0)


def test_the_environment_selector_ships_with_local_as_its_default(tmp_path: Path) -> None:
    html = _fetch(tmp_path, "/")[0]
    assert (
        '<select id="env" data-testid="replay.environment"><option value="local">local</option>'
        in html
    )
    for testid in ("replay.env-refresh", "replay.env-reason"):
        assert f'data-testid="{testid}"' in html


def test_the_scenario_set_and_run_set_panel_show_only_in_a_device_cloud_environment(
    tmp_path: Path,
) -> None:
    html = _fetch(tmp_path, "/")[0]
    for testid in ("replay.scenario-set", "replay.runset"):
        assert "cloudonly" in _tag(html, testid), testid
    for testid in (
        "replay.scenarios",
        "replay.all-scenarios",
        "replay.runset-stop",
        "replay.runset-partial",
    ):
        assert f'data-testid="{testid}"' in html
    assert "multiple" in _tag(html, "replay.scenarios")


def test_the_fields_a_fan_out_ignores_hide_in_a_device_cloud_environment(tmp_path: Path) -> None:
    # run-set reads none of these, and the Readiness checks probe the serve host, not the cloud.
    html = _fetch(tmp_path, "/")[0]
    for testid in ("replay.simulators", "replay.doctor", "replay.scenario", "replay.view-scenario"):
        assert "localonly" in _tag(html, testid), testid
    for element in ('id="erasedev"', 'id="headed"'):
        checks = html[: html.index(element)].rsplit('<div class="checks', 1)[1]
        assert checks.split('"', 1)[0].endswith("localonly"), element


def test_the_hiding_composes_with_the_platform_toggle(tmp_path: Path) -> None:
    # syncPlatform sets an inline display on .iosonly/.webonly, so the device-cloud hiding is a
    # class on the view with !important: either one hiding an element keeps it hidden.
    css = _fetch(tmp_path, "/")[0]  # serve.css ships inlined in the index
    # The grade badge is named by id: renderGradeBadge rewrites its className, dropping any class.
    assert (
        "#view-replay.cloud .localonly,#view-replay.cloud #scn-grade{display:none!important}" in css
    )
    assert "#view-replay:not(.cloud) .cloudonly{display:none!important}" in css


def test_the_form_lists_batch_environments_alone(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "getJSON('/api/environments'" in js
    assert "e.kind==='batch'" in js


def test_run_posts_explicit_scenario_names_to_run_set(tmp_path: Path) -> None:
    js, panels = _fetch(tmp_path, "/serve.runset.mjs", "/serve.panels.mjs")
    assert "postJSON('/api/run-set',{target:$('#target').value,environment,scenarios}" in js
    # All scenarios sends every listed name rather than omitting the field.
    assert "scnFiles.map(s=>s.file)" in js
    assert "if(cloudEnvironment())return dispatchRunSet();" in panels


def test_rows_poll_the_job_without_its_log_and_stream_one_row_at_a_time(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "'/api/jobs/'+encodeURIComponent(row.id)+'?lines=0'" in js
    # A worker's failure payload carries no status, so ok===false or an error ends a row too.
    assert "v.status==='done'||v.ok===false||!!v.error" in js
    assert js.count("streamJob(") == 1
    assert "closeStream(set);" in js


def test_stop_all_cancels_every_unfinished_row(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "set.rows.filter(r=>!r.terminal).forEach(r=>cancelJob(" in js
