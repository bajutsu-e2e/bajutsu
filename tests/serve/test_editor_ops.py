"""Tests for the scenario editor operations layer (BE-0013, Slice 1).

Operations-level tests — resolve a pick against stored elements.json, no live driver.
"""

from __future__ import annotations

import json
from pathlib import Path

from _shared import FakeObjectStore, project

from bajutsu.serve import operations as ops
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.state import ServeState


def _elements() -> list[dict[str, object]]:
    """A small fake element tree matching test_capture_ops._screen()."""
    return [
        {
            "identifier": None,
            "label": None,
            "traits": ["window"],
            "value": None,
            "frame": [0.0, 0.0, 320.0, 568.0],
            "nativeZ": None,
        },
        {
            "identifier": "auth.email",
            "label": "Email",
            "traits": ["textField"],
            "value": None,
            "frame": [20.0, 100.0, 280.0, 30.0],
            "nativeZ": None,
        },
        {
            "identifier": "auth.password",
            "label": "Password",
            "traits": ["textField"],
            "value": None,
            "frame": [20.0, 150.0, 280.0, 30.0],
            "nativeZ": None,
        },
        {
            "identifier": "auth.submit",
            "label": "Login",
            "traits": ["button"],
            "value": None,
            "frame": [100.0, 220.0, 120.0, 44.0],
            "nativeZ": None,
        },
    ]


def _write_step_elements(runs: Path, run_id: str, step_id: str) -> None:
    """Write elements.json for one step under the run directory."""
    step_dir = runs / run_id / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")


def _state(tmp_path: Path) -> tuple[ServeState, Path]:
    scn_dir, cfg, runs = project(tmp_path)
    state = ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path)
    return state, runs


# ---------------------------------------------------------------------------
# resolve_scenario_pick — happy path
# ---------------------------------------------------------------------------


def test_resolve_pick_returns_selector(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    _write_step_elements(runs, "20260629-100000", "00-s/step0")

    payload, status = ops.resolve_scenario_pick(
        state,
        {
            "target": "demo",
            "runId": "20260629-100000",
            "stepId": "00-s/step0",
            "point": [0.5, 0.41],  # inside auth.submit (100-220, 220-264 on 320x568)
        },
    )
    assert status == 200
    assert payload["selector"]["id"] == "auth.submit"
    assert payload["rung"] == "id"
    assert payload.get("ambiguous") is None


def test_resolve_pick_label_fallback(tmp_path: Path) -> None:
    """When an element has no id, the resolver falls to label rung."""
    state, runs = _state(tmp_path)
    elements = [
        {
            "identifier": None,
            "label": "Continue",
            "traits": ["button"],
            "value": None,
            "frame": [50.0, 50.0, 100.0, 44.0],
            "nativeZ": None,
        },
    ]
    step_dir = runs / "run1" / "00-s/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(elements), encoding="utf-8")

    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.8, 0.7]},
    )
    assert status == 200
    assert payload["selector"]["label"] == "Continue"
    assert payload["rung"] == "label"


# ---------------------------------------------------------------------------
# resolve_scenario_pick — ambiguity
# ---------------------------------------------------------------------------


def test_resolve_pick_ambiguous(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    dup_elements = [
        {
            "identifier": "dup",
            "label": "A",
            "traits": ["button"],
            "value": None,
            "frame": [10.0, 10.0, 80.0, 44.0],
            "nativeZ": None,
        },
        {
            "identifier": "dup",
            "label": "B",
            "traits": ["button"],
            "value": None,
            "frame": [10.0, 60.0, 80.0, 44.0],
            "nativeZ": None,
        },
    ]
    step_dir = runs / "run1" / "00-s/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(dup_elements), encoding="utf-8")

    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 200
    assert payload["ambiguous"] is True


# ---------------------------------------------------------------------------
# resolve_scenario_pick — refusal / errors
# ---------------------------------------------------------------------------


def test_resolve_pick_no_actionable_element(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    empty_elements = [
        {
            "identifier": None,
            "label": None,
            "traits": ["window"],
            "value": None,
            "frame": [0.0, 0.0, 320.0, 568.0],
            "nativeZ": None,
        },
    ]
    step_dir = runs / "run1" / "00-s/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(empty_elements), encoding="utf-8")

    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 200
    assert payload.get("refused") is not None


def test_resolve_pick_reads_elements_from_object_storage(tmp_path: Path) -> None:
    """A hosted backend (`ObjectStorageArtifactStore`) resolves a pick the same as local `serve`
    (BE-0258): before this fix, `resolve_scenario_pick` read `state.runs_dir` directly and always
    404'd here, even though the elements were present in object storage."""
    state, _runs = _state(tmp_path)
    key = "run1/00-s/step0/elements.json"
    state.artifacts = ObjectStorageArtifactStore(  # type: ignore[assignment]
        FakeObjectStore({key: json.dumps(_elements()).encode()}), prefix=""
    )

    payload, status = ops.resolve_scenario_pick(
        state,
        {
            "target": "demo",
            "runId": "run1",
            "stepId": "00-s/step0",
            "point": [0.5, 0.41],
        },
    )
    assert status == 200
    assert payload["selector"]["id"] == "auth.submit"


def test_resolve_pick_missing_elements_file(tmp_path: Path) -> None:
    state, _runs = _state(tmp_path)
    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 404
    assert "elements" in payload["error"]


def test_resolve_pick_requires_config(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", config=None)
    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 400
    assert "config" in payload["error"]


def test_resolve_pick_invalid_run_id(tmp_path: Path) -> None:
    state, _runs = _state(tmp_path)
    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "../escape", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 400
    assert "run" in payload["error"].lower()


def test_resolve_pick_invalid_point(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    _write_step_elements(runs, "run1", "00-s/step0")

    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": "bad"},
    )
    assert status == 400
    assert "point" in payload["error"]


def test_resolve_pick_stepid_traversal_rejected(tmp_path: Path) -> None:
    """stepId with '..' must be rejected to prevent path traversal."""
    state, _runs = _state(tmp_path)
    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "../../etc/passwd", "point": [0.5, 0.5]},
    )
    assert status == 400
    assert "step" in payload["error"].lower()


def test_resolve_pick_stepid_absolute_rejected(tmp_path: Path) -> None:
    state, _runs = _state(tmp_path)
    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "/etc/passwd", "point": [0.5, 0.5]},
    )
    assert status == 400
    assert "step" in payload["error"].lower()


def test_resolve_pick_corrupt_elements(tmp_path: Path) -> None:
    """Corrupt elements.json should return a controlled error, not a 500."""
    state, runs = _state(tmp_path)
    step_dir = runs / "run1" / "00-s/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text("not json", encoding="utf-8")

    payload, status = ops.resolve_scenario_pick(
        state,
        {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
    )
    assert status == 400
    assert "elements" in payload["error"].lower()


# ---------------------------------------------------------------------------
# read_scenario with runId — step artifact handles (BE-0013)
# ---------------------------------------------------------------------------

SCENARIO_YAML = """\
- name: login
  steps:
    - tap: { id: auth.email }
    - type: { into: { id: auth.password }, text: secret }
    - tap: { id: auth.submit }
"""


def _write_run_with_steps(runs: Path, run_id: str, sid: str, step_ids: list[str]) -> None:
    """Write a minimal run with manifest + per-step artifacts.

    The manifest's own `artifacts` list is what `_step_artifacts` now resolves names from
    (BE-0341), so each step's entry names the pre-step baseline files this fixture writes. The
    baseline alone is what a step records under a capture policy asking for no post-step screenshot,
    so `before.png` is the only screenshot here and the picker has nothing else to choose.
    """
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "runId": run_id,
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": sid,
                "steps": [
                    {
                        "index": i,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": f"{step_id}/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": f"{step_id}/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                    for i, step_id in enumerate(step_ids)
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for step_id in step_ids:
        step_dir = run_dir / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
        # Write a tiny placeholder for before.png
        (step_dir / "before.png").write_bytes(b"PNG")


def test_read_scenario_with_run_returns_steps(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    # Write the scenario YAML
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    # Write run artifacts
    _write_run_with_steps(
        runs, "run1", "00-login", ["00-login/step0", "00-login/step1", "00-login/step2"]
    )

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert "yaml" in payload
    assert "steps" in payload
    steps = payload["steps"]
    assert len(steps) == 3
    assert steps[0]["stepId"] == "00-login/step0"
    assert steps[0]["screenshotUrl"].endswith("/before.png")
    assert steps[0]["elementsUrl"].endswith("/elements.json")


def test_read_scenario_prefers_the_post_action_screenshot_for_the_picker(tmp_path: Path) -> None:
    """A step that recorded both screenshots pairs the picker with `after.png`, matching the HTML
    report — the editor resolves a click against `elements.json`, which the same post-step capture
    that wrote `after.png` left describing the post-action screen."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    (step_dir / "before.png").write_bytes(b"PNG")
    (step_dir / "after.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        # Capture order: the pre-step baseline, then the post-step capture.
                        "artifacts": [
                            {
                                "name": "00-login/step0/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/after.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state, "demo", str(scn_dir / "login.yaml"), run_id="run1", scenario_name="login"
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/after.png"
    assert payload["steps"][0]["elementsUrl"] == "/runs/run1/00-login/step0/elements.json"


def test_read_scenario_falls_back_when_the_post_action_screenshot_is_missing(
    tmp_path: Path,
) -> None:
    """The manifest can name a file the store no longer holds — a run restored from Trash, or one
    synced into an object store that never received the last write. The picker then falls back to
    the `before.png` beside it rather than going inert: the choice is made among the names that
    exist, not among every name recorded (review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    (step_dir / "before.png").write_bytes(b"PNG")  # `after.png` is recorded below but never written
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/step0/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/after.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state, "demo", str(scn_dir / "login.yaml"), run_id="run1", scenario_name="login"
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/before.png"


def test_read_scenario_with_run_resolves_manifest_recorded_names_not_hardcoded(
    tmp_path: Path,
) -> None:
    """`_step_artifacts` resolves whatever name the run actually recorded (BE-0341) — proven with a
    name that would not match if the lookup still assumed a fixed `before.png` / `after.png`."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    # A capturePolicy rule's own screenshot, not the pre-step baseline's `before.png` — proves the
    # lookup reads the manifest's recorded name rather than assuming one.
    (step_dir / "around.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/step0/around.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/around.png"


def test_read_scenario_with_run_resolves_a_named_step_after_nested_control_flow(
    tmp_path: Path,
) -> None:
    """A named top-level step *after* an `if` block resolves its own artifacts, not the nested
    step's (BE-0341 review follow-up). The runner's own step-name fallback counts every executed
    step, including nested ones, so the `if` block's one nested `tap` consumes an index the
    top-level loop below never sees; keying the lookup by each outcome's own recorded artifact path
    (this step's real runtime id) rather than by top-level position keeps the two YAML steps'
    artifacts from being swapped."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(
        """\
- name: login
  steps:
    - name: gate
      if:
        condition: { exists: { id: auth.email } }
        then:
          - name: inner
            tap: { id: auth.email }
    - name: after
      tap: { id: auth.submit }
""",
        encoding="utf-8",
    )

    run_dir = runs / "run1"
    for step_id in ("inner", "after"):
        step_dir = run_dir / "00-login" / step_id
        step_dir.mkdir(parents=True)
        (step_dir / "before.png").write_bytes(b"PNG")
        (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {"index": 0, "action": "if", "ok": True, "artifacts": []},
                    {
                        "index": 1,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/inner/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/inner/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    },
                    {
                        "index": 2,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/after/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/after/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    },
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    after_step = next(s for s in payload["steps"] if s["stepId"] == "00-login/after")
    assert after_step["screenshotUrl"] == "/runs/run1/00-login/after/before.png"
    assert after_step["elementsUrl"] == "/runs/run1/00-login/after/elements.json"


def test_read_scenario_with_run_prefers_the_first_recorded_screenshot(tmp_path: Path) -> None:
    """When a step's artifacts list carries two `screenshot`-kind entries — the pre-step baseline
    plus a capturePolicy rule's own shot, as a real run under an active rule produces — the first
    one recorded (the baseline) wins, mirroring `report/rows.py`'s `by_kind.setdefault` precedence
    (BE-0341). Locks in the documented floor/ceiling precedence rather than leaving it untested."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    (step_dir / "before.png").write_bytes(b"PNG")
    (step_dir / "around.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/step0/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/around.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/before.png"


def test_read_scenario_with_run_missing_artifacts(tmp_path: Path) -> None:
    """Steps without artifacts on disk get null URLs."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    # Write manifest but no step directories
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": "00-login", "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    steps = payload["steps"]
    assert len(steps) == 3
    for s in steps:
        assert s["screenshotUrl"] is None
        assert s["elementsUrl"] is None


def test_read_scenario_with_run_empty_sid_yields_no_steps(tmp_path: Path) -> None:
    """A scenario record whose `sid` is `""` (malformed/partially written) bails to no steps, the
    same as a missing `sid` — restoring the coercion the old `_find_sid` applied (BE-0341)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": "", "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"] == []


def test_read_scenario_with_run_rejects_a_non_string_sid(tmp_path: Path) -> None:
    """A scenario record whose `sid` is not a `str` (e.g. an int, from a malformed manifest)
    bails to no steps rather than flowing into an f-string-built `stepId` (review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": 123, "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"] == []


def test_read_scenario_with_run_rejects_a_path_traversal_sid(tmp_path: Path) -> None:
    """A scenario record whose `sid` is a `..`-shaped traversal attempt (a malformed manifest, or
    one from an untrusted source) bails to no steps rather than building a `stepId` that would
    later be rejected by `resolve_scenario_pick`'s own `_valid_step_id` check anyway — better to
    never produce one in the first place (review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": "..", "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"] == []


def test_read_scenario_with_run_rejects_a_traversal_shaped_artifact_name(tmp_path: Path) -> None:
    """An artifact `name` that is a `str` but `..`-shaped (a malformed/tampered manifest) is
    rejected rather than flowing into `/runs/<run_id>/<name>` and an `ArtifactStore.exists()` probe
    — `LocalArtifactStore` only enforces containment to `runs_dir` as a whole, not per-run, so a
    traversal name can otherwise resolve to a *different* run's artifact within the same org's
    runs tree (review follow-up). Proven by content, not just a null check: a real file at that
    escaped path exists on disk, under a second run this step never belongs to. The step's own
    valid `elements` entry still establishes the step-id key; only the malformed `screenshot`
    entry is rejected."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    # A second, unrelated run whose artifact the traversal below targets — proves an escape to
    # another run within `runs_dir`, not merely a nonexistent path that would resolve to `None`
    # regardless of validation.
    other_run_dir = runs / "run2" / "00-other/step0"
    other_run_dir.mkdir(parents=True)
    (other_run_dir / "secret.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/../../../run2/00-other/step0/secret.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["elementsUrl"] == "/runs/run1/00-login/step0/elements.json"
    assert payload["steps"][0]["screenshotUrl"] is None


def test_read_scenario_with_run_ignores_non_string_artifact_fields(tmp_path: Path) -> None:
    """A malformed manifest entry whose `kind`/`name` are not strings degrades to "no link" rather
    than flowing a non-string value into the URL built from it (BE-0341)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {"name": 123, "kind": "screenshot", "provider": "driver"},
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": None,
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] is None
    assert payload["steps"][0]["elementsUrl"] is None


def test_read_scenario_with_run_ignores_non_dict_artifact_entries(tmp_path: Path) -> None:
    """A step's `artifacts` list carrying a non-`dict` entry (a malformed/partially written
    manifest) degrades to "no link" for that entry rather than raising when `_artifact_names`
    calls `.get` on it — and the step id is still derived from the first entry that has a valid
    `name`, skipping past the leading non-`dict` one (BE-0341 review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "before.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            "not-a-dict",
                            {
                                "name": "00-login/step0/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/before.png"


def test_read_scenario_with_run_ignores_non_dict_scenario_entries(tmp_path: Path) -> None:
    """A manifest whose `scenarios` list carries a non-`dict` entry ahead of the real record
    degrades to skipping it rather than raising on `.get` (BE-0341 review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            "not-a-dict",
            {"scenario": "login", "ok": True, "sid": "00-login", "steps": []},
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["stepId"] == "00-login/step0"


def test_read_scenario_with_run_ignores_a_non_dict_manifest(tmp_path: Path) -> None:
    """A `manifest.json` that parses as JSON but isn't an object (e.g. a bare list) degrades to an
    empty step list rather than raising on the first `.get()` (BE-0341 review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"] == []


def test_read_scenario_with_run_ignores_a_non_list_steps_field(tmp_path: Path) -> None:
    """A scenario record whose `steps` field is `null` (or otherwise not a list) degrades to no
    artifacts for any step rather than raising when iterated (BE-0341 review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": "00-login", "steps": None}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] is None


def test_read_scenario_with_run_skips_a_slash_less_name_for_a_later_valid_one(
    tmp_path: Path,
) -> None:
    """A `dict` artifact with a `str` `name` that carries no step-id prefix (e.g. a fallback
    filename recorded for a path outside the run dir) must not stop the step-id search before a
    later artifact with a real, usable name (BE-0341 review follow-up)."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "before.png").write_bytes(b"PNG")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {"name": "orphan.png", "kind": "note", "provider": "driver"},
                            {
                                "name": "00-login/step0/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["screenshotUrl"] == "/runs/run1/00-login/step0/before.png"


def test_read_scenario_with_run_does_not_let_a_traversal_name_hijack_the_step_key(
    tmp_path: Path,
) -> None:
    """A `..`-shaped artifact `name` (a malformed/tampered manifest) recorded *first* for a step
    must not become that step's lookup key: `name.rsplit("/", 1)[0]` on it would produce a key no
    real `step_id` ever matches, hiding every other, legitimate artifact recorded for the same step
    (review follow-up). The step's own valid `elements` entry — recorded second — still establishes
    the correct key."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    step_dir = run_dir / "00-login/step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": "00-login/step0/../../../run2/00-other/step0/secret.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"][0]["elementsUrl"] == "/runs/run1/00-login/step0/elements.json"
    assert payload["steps"][0]["screenshotUrl"] is None


def test_read_scenario_with_run_reads_from_object_storage(tmp_path: Path) -> None:
    """A hosted backend (`ObjectStorageArtifactStore`) populates the per-step artifact list the
    same as local `serve` (BE-0258): before this fix, `_step_artifacts` read `state.runs_dir`
    directly and always returned an empty list here, even though the manifest and per-step
    artifacts were present in object storage."""
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [
            {
                "scenario": "login",
                "ok": True,
                "sid": "00-login",
                "steps": [
                    {
                        "index": i,
                        "action": "tap",
                        "ok": True,
                        "artifacts": [
                            {
                                "name": f"00-login/step{i}/before.png",
                                "kind": "screenshot",
                                "provider": "driver",
                            },
                            {
                                "name": f"00-login/step{i}/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            },
                        ],
                    }
                    for i in range(3)
                ],
            }
        ],
    }
    objects = {"run1/manifest.json": json.dumps(manifest).encode()}
    for i in range(3):
        step_id = f"00-login/step{i}"
        objects[f"run1/{step_id}/elements.json"] = json.dumps(_elements()).encode()
        objects[f"run1/{step_id}/before.png"] = b"PNG"
    state.artifacts = ObjectStorageArtifactStore(  # type: ignore[assignment]
        FakeObjectStore(objects), prefix=""
    )

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    steps = payload["steps"]
    assert len(steps) == 3
    assert steps[0]["stepId"] == "00-login/step0"
    assert steps[0]["screenshotUrl"] == "/runs/run1/00-login/step0/before.png"
    assert steps[0]["elementsUrl"] == "/runs/run1/00-login/step0/elements.json"


def test_read_scenario_without_run_derives_steps_from_yaml(tmp_path: Path) -> None:
    """Without runId, steps are derived from the scenario YAML itself (BE-0262).

    The Author Edit picker needs a step list even for a scenario that has never run, so a live
    session can target a step to fix; the per-step screenshot/elements URLs are absent because there
    is no stored run — the live path supplies the current screenshot instead.
    """
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
    )
    assert status == 200
    assert "yaml" in payload
    steps = payload["steps"]
    assert len(steps) == 3
    assert [s["action"] for s in steps] == ["tap", "type", "tap"]
    for s in steps:
        assert s["screenshotUrl"] is None
        assert s["elementsUrl"] is None


def test_read_scenario_without_run_returns_structural_scenarios(tmp_path: Path) -> None:
    """With structure opt-in but no runId, the viewer (BE-0273) gets the runner's per-scenario
    parse: every named scenario with its ordered steps' action/fields — no run, no run-scoped URLs."""
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        structure=True,
    )
    assert status == 200
    scenarios = payload["scenarios"]
    assert [s["name"] for s in scenarios] == ["login"]
    steps = scenarios[0]["steps"]
    assert [s["action"] for s in steps] == ["tap", "type", "tap"]
    assert steps[0]["fields"] == {"id": "auth.email"}
    assert steps[1]["fields"]["into"] == {"id": "auth.password"}
    # The structural view is run-independent, so it never carries the run-scoped artifact URLs.
    assert "elementsUrl" not in steps[0]
    assert "screenshotUrl" not in steps[0]


def test_read_scenario_without_run_covers_all_named_scenarios(tmp_path: Path) -> None:
    """A file with several named scenarios yields one structural entry per scenario (the Replay
    picker is per-file), unlike the run-scoped `steps` which resolves a single scenario."""
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "multi.yaml").write_text(
        "- name: first\n"
        "  description: the first\n"
        "  steps:\n"
        "    - tap: { id: a.one }\n"
        "- name: second\n"
        "  steps:\n"
        "    - tap: { id: b.two }\n",
        encoding="utf-8",
    )

    payload, status = ops.read_scenario(state, "demo", str(scn_dir / "multi.yaml"), structure=True)
    assert status == 200
    scenarios = payload["scenarios"]
    assert [s["name"] for s in scenarios] == ["first", "second"]
    assert scenarios[0]["description"] == "the first"
    assert scenarios[0]["steps"][0]["fields"] == {"id": "a.one"}
    assert scenarios[1]["steps"][0]["fields"] == {"id": "b.two"}


def test_read_scenario_without_run_unparseable_yaml_has_no_structure(tmp_path: Path) -> None:
    """Malformed YAML still returns its raw text (the authoritative view) with an empty
    structure — the viewer falls back to raw rather than failing the request."""
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "broken.yaml").write_text("this: is: not: a: scenario\n", encoding="utf-8")

    payload, status = ops.read_scenario(state, "demo", str(scn_dir / "broken.yaml"), structure=True)
    assert status == 200
    assert "yaml" in payload
    assert payload["scenarios"] == []


def test_read_scenario_with_run_defaults_to_first_scenario(tmp_path: Path) -> None:
    """When scenario_name is omitted, default to the first scenario in the YAML."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    _write_run_with_steps(
        runs, "run1", "00-login", ["00-login/step0", "00-login/step1", "00-login/step2"]
    )

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
    )
    assert status == 200
    assert len(payload["steps"]) == 3


def test_read_scenario_with_traversal_run_id(tmp_path: Path) -> None:
    """A run_id with '..' must not escape runs_dir."""
    state, _runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="../escape",
    )
    assert status == 200
    assert payload["steps"] == []


def test_read_scenario_with_run_no_matching_scenario(tmp_path: Path) -> None:
    """When the named scenario isn't in the manifest, steps are empty."""
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    run_dir = runs / "run1"
    run_dir.mkdir(parents=True)
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "other", "ok": True, "sid": "00-other", "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    assert payload["steps"] == []


# ---------------------------------------------------------------------------
# step artifacts include action + fields (BE-0013, Slice 3)
# ---------------------------------------------------------------------------


def test_step_artifacts_include_action_and_fields(tmp_path: Path) -> None:
    state, runs = _state(tmp_path)
    scn_dir = tmp_path / "scenarios"
    (scn_dir / "login.yaml").write_text(SCENARIO_YAML, encoding="utf-8")
    _write_run_with_steps(
        runs, "run1", "00-login", ["00-login/step0", "00-login/step1", "00-login/step2"]
    )

    payload, status = ops.read_scenario(
        state,
        "demo",
        str(scn_dir / "login.yaml"),
        run_id="run1",
        scenario_name="login",
    )
    assert status == 200
    steps = payload["steps"]
    # step 0: tap with id selector
    assert steps[0]["action"] == "tap"
    assert steps[0]["fields"] == {"id": "auth.email"}
    # step 1: type with selector + text
    assert steps[1]["action"] == "type"
    assert steps[1]["fields"]["into"] == {"id": "auth.password"}
    assert steps[1]["fields"]["text"] == "secret"
    # step 2: tap with id selector
    assert steps[2]["action"] == "tap"
    assert steps[2]["fields"] == {"id": "auth.submit"}
