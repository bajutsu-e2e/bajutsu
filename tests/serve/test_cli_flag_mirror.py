"""serve derives its launch argv from the CLI's own option metadata (BE-0134).

These lock the single-source-of-truth contract: every `run`/`record`/`crawl` flag is classified
(so a new CLI flag can't be silently unreachable from serve), an unknown name is rejected (so a
renamed/removed flag is caught), and `_render` maps values to the right on/off form.
"""

from __future__ import annotations

import pytest

from bajutsu.serve import _cli_flags as cf


def _option_names(name: cf.Command) -> set[str]:
    return set(cf.option_names(name))


# --- completeness: every CLI flag is classified, so a new one can't slip through unnoticed ---


def test_run_flag_surface_is_fully_classified() -> None:
    # Base args run_command emits by position, and the one flag serve intentionally does not expose
    # (evidence_store is env-driven, BAJUTSU_EVIDENCE_STORE). Everything else must be pass-through-able.
    base_handled = {"target_name", "scenario", "config", "progress"}
    not_serve_exposed = {"evidence_store"}
    pass_through = {
        "backend",
        "udid",
        "workers",
        "erase",
        "dismiss_alerts",
        "headed",
        "baselines",
        "runs_dir",
        "upload_exec",
        "browser",
        "browsers",
        "tag",
        "exclude",
        "schemas",
        "goldens",
        "network",
        "log_predicate",
        "log_subsystem",
        "alert_instruction",
        "zip_run",
        "config_offline",
        "require_pinned_config",
    }
    buckets = [base_handled, not_serve_exposed, pass_through]
    # disjoint
    for i, a in enumerate(buckets):
        for b in buckets[i + 1 :]:
            assert not (a & b), f"buckets overlap: {a & b}"
    # exhaustive: a new CLI flag lands here and fails this until classified.
    assert base_handled | not_serve_exposed | pass_through == _option_names("run")
    # every pass-through name is a real flag_args can render (no typo in the list above).
    assert not (pass_through - _option_names("run"))


def test_record_flag_surface_is_fully_classified() -> None:
    base_handled = {"target_name", "goal", "config", "out"}
    # name is redundant (serve computes --out); browser/alert_instruction aren't exposed via serve yet.
    not_serve_exposed = {"name", "browser", "alert_instruction"}
    pass_through = {"backend", "udid", "erase", "dismiss_alerts", "headed", "upload_exec"}
    # serve forces --handoff to `stream` (a human at the browser answers over SSE), never a knob (BE-0179).
    serve_forced = {"handoff"}
    assert base_handled | not_serve_exposed | pass_through | serve_forced == _option_names("record")


def test_crawl_flag_surface_is_fully_classified() -> None:
    base_handled = {"target_name", "out", "config", "max_screens", "max_steps"}
    not_serve_exposed = {"prune_global", "alert_instruction"}
    pass_through = {
        "backend",
        "udid",
        "workers",
        "erase",
        "dismiss_alerts",
        "headed",
        "resume_src",
        "resume_key",
        "upload_exec",
    }
    assert base_handled | not_serve_exposed | pass_through == _option_names("crawl")


def test_triage_flag_surface_is_fully_classified() -> None:
    # run_dir is a positional argument (not an option); --config is passed directly by triage_command.
    base_handled = {"config"}
    # The CLI-only apply/rerun flow: serve previews with --apply then writes through POST /api/scenario,
    # so it never drives --write/--rerun (nor the --rerun-only backend/udid), passes the run dir
    # positionally rather than via --runs, and triages the run's first failed scenario rather than
    # forwarding a --scenario name filter (BE-0147).
    not_serve_exposed = {"runs", "write", "rerun", "backend", "udid", "scenario"}
    pass_through = {"target_name", "ai", "apply", "json_out"}
    assert base_handled | not_serve_exposed | pass_through == _option_names("triage")


# --- drift: an unknown param name is rejected (catches a renamed/removed CLI flag) ---


def test_flag_args_rejects_unknown_param() -> None:
    with pytest.raises(ValueError, match="not an option"):
        cf.flag_args("run", {"definitely_not_a_flag": "x"})


# --- rendering: value maps to the right on/off form ---


def test_render_bool_flag_pair() -> None:
    assert cf.flag_args("run", {"erase": True}) == ["--erase"]
    assert cf.flag_args("run", {"erase": False}) == ["--no-erase"]
    assert cf.flag_args("run", {"erase": None}) == []  # tri-state: leave the default
    # An empty string is the "unset" sentinel for every option, bool flags included — it must not
    # be read as the off side (which would force --no-erase and silently wipe the device).
    assert cf.flag_args("run", {"erase": ""}) == []


def test_render_store_true_flag_cannot_force_off() -> None:
    # --zip has no --no-zip; True emits it, False/None emit nothing (can't be forced off).
    assert cf.flag_args("run", {"zip_run": True}) == ["--zip"]
    assert cf.flag_args("run", {"zip_run": False}) == []
    assert cf.flag_args("run", {"zip_run": None}) == []


def test_render_value_option_omits_empty() -> None:
    assert cf.flag_args("run", {"tag": "smoke"}) == ["--tag", "smoke"]
    assert cf.flag_args("run", {"tag": ""}) == []  # serve's "unset" sentinel
    assert cf.flag_args("run", {"tag": None}) == []
