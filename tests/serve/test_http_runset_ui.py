"""Structural tests for the Replay tab's device-cloud form and run-set panel (BE-0465 units 2-3).

The markup ships inlined in the index; the JS ships as the serve.runset.mjs ES module (BE-0247).
The dispatch, environment listing and job read are covered by their operation tests; here we pin
that the form's controls exist, which local-only controls it hides, and that the module talks to the
endpoints the design names — run-set with explicit names, and a status poll without the log.
"""

from __future__ import annotations

import json
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


def test_stop_all_cancels_every_unfinished_row_and_reports_each_failure(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    # cancelJob swallows a refused cancel, so Stop all posts its own and reads each answer.
    assert "cancelJob" not in js
    assert "const rows=set.rows.filter(r=>!r.terminal||r.lost);" in js
    assert "'/api/jobs/'+encodeURIComponent(row.id)+'/cancel',{method:'POST'}" in js
    assert "if(!res.ok)return (v&&v.error)||'HTTP '+res.status;" in js
    assert "r.cancelError=errs[i]?`cancel failed: ${errs[i]}`:'';" in js
    # A failed cancel leaves Stop all clickable so the user can try again.
    assert "if(!failed.length){if(!runSetLive())stop.textContent=stop.dataset.idle;return;}" in js
    assert "stop.disabled=false;" in js


def test_a_failing_status_poll_is_shown_and_gives_up_after_five_tries(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "const MAX_POLL_ERRORS=5;" in js
    assert "pollFailed(set,row,'HTTP '+res.status);" in js
    assert "row.lost=row.pollErrors>=MAX_POLL_ERRORS;" in js
    assert "`status check failed: ${why}${row.lost?'':' — retrying'}`" in js
    assert "row.lost?['unknown','ng']:rowState(v)" in js
    # A good read resets the count.
    assert "row.pollErrors=0;" in js


def test_a_404_poll_ends_the_row_unknown_and_keeps_it_cancellable(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    # A 404 means this server lost track of the job, not that the job failed.
    assert "if(res.ok&&v&&typeof v==='object'){applyView(row,v);return;}" in js
    assert "res.status===404" in js
    assert "row.lost=true;row.terminal=true;" in js
    assert "this server no longer tracks the job (restart or another replica)" in js
    # Stop all still cancels a lost row, and stays clickable while one is listed.
    assert "const rows=set.rows.filter(r=>!r.terminal||r.lost);" in js
    assert "if(!set.rows.some(r=>r.lost))" in js
    # Once every row has ended, the button leaves "Stopping…" even when a lost row keeps it enabled.
    assert (
        "if(set.rows.every(r=>r.terminal)){const stop=$('#runset-stop');"
        "stop.textContent=stop.dataset.idle;" in js
    )


def test_a_failed_environment_read_keeps_the_previous_list(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "getJSON('/api/environments',FETCH_ERROR)" in js
    assert "Could not load environments — Refresh to retry" in js


def test_a_second_fan_out_waits_for_the_first(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "a run-set is still running — Stop all or wait" in js


def test_a_reconnecting_row_stream_clears_its_log(tmp_path: Path) -> None:
    # The stream replays the log from line 1 on every (re)connect, so lines must not double.
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "es.addEventListener('open',()=>{log.textContent='';});" in js


def test_a_cancelled_row_reads_cancelling_until_it_finishes(tmp_path: Path) -> None:
    js = _fetch(tmp_path, "/serve.runset.mjs")[0]
    assert "if(v.cancelled)return v.status==='done'?['cancelled','ng']:['cancelling…','run'];" in js


def test_a_live_run_set_stays_visible_in_local_mode(tmp_path: Path) -> None:
    html, js = _fetch(tmp_path, "/", "/serve.runset.mjs")
    # Switching Environment back to local must not hide a fan-out still running, nor its Stop all.
    assert "#view-replay.runset-live #runset{display:flex!important}" in html
    assert "return !!runSet&&runSet.rows.some(r=>!r.terminal);" in js
    assert "$('#view-replay').classList.toggle('runset-live',runSetLive());" in js


def test_job_poll_without_log_over_http(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    job = srv.Job(id="j", cmd=[])
    job.lines.append("step ok\n")
    state.jobs["j"] = job
    server, port = _serve(state)
    try:
        polled = json.loads(_get(port, "/api/jobs/j?lines=0")[1])
        full = json.loads(_get(port, "/api/jobs/j")[1])
    finally:
        server.shutdown()
        server.server_close()
    assert "lines" not in polled and polled["id"] == "j"
    assert "lines" in full


def test_environments_route_is_empty_without_a_repository(tmp_path: Path) -> None:
    body = _fetch(tmp_path, "/api/environments")[0]
    assert json.loads(body) == {"environments": []}
