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
        },
        {
            "identifier": "auth.email",
            "label": "Email",
            "traits": ["textField"],
            "value": None,
            "frame": [20.0, 100.0, 280.0, 30.0],
        },
        {
            "identifier": "auth.password",
            "label": "Password",
            "traits": ["textField"],
            "value": None,
            "frame": [20.0, 150.0, 280.0, 30.0],
        },
        {
            "identifier": "auth.submit",
            "label": "Login",
            "traits": ["button"],
            "value": None,
            "frame": [100.0, 220.0, 120.0, 44.0],
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
        },
        {
            "identifier": "dup",
            "label": "B",
            "traits": ["button"],
            "value": None,
            "frame": [10.0, 60.0, 80.0, 44.0],
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
    """Write a minimal run with manifest + per-step artifacts."""
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
                    {"index": i, "action": "tap", "ok": True, "artifacts": []}
                    for i in range(len(step_ids))
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for step_id in step_ids:
        step_dir = run_dir / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
        # Write a tiny placeholder for after.png
        (step_dir / "after.png").write_bytes(b"PNG")


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
    assert steps[0]["screenshotUrl"].endswith("/after.png")
    assert steps[0]["elementsUrl"].endswith("/elements.json")


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
                    {"index": i, "action": "tap", "ok": True, "artifacts": []} for i in range(3)
                ],
            }
        ],
    }
    objects = {"run1/manifest.json": json.dumps(manifest).encode()}
    for i in range(3):
        step_id = f"00-login/step{i}"
        objects[f"run1/{step_id}/elements.json"] = json.dumps(_elements()).encode()
        objects[f"run1/{step_id}/after.png"] = b"PNG"
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
    assert steps[0]["screenshotUrl"] == "/runs/run1/00-login/step0/after.png"
    assert steps[0]["elementsUrl"] == "/runs/run1/00-login/step0/elements.json"


def test_read_scenario_without_run_returns_yaml_only(tmp_path: Path) -> None:
    """Without runId, the response is the plain {yaml} — no steps."""
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
    assert "steps" not in payload


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
