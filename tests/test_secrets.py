"""Tests for secret variables: config resolution, value-based redaction, and the
run-time interpolation that keeps the value out of the recorded scenario."""

from __future__ import annotations

from bajutsu.config import load_config, resolve
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import Redact, Scenario, Step, TypeText

# --- config ---


def test_secrets_union_defaults_and_app() -> None:
    cfg = load_config(
        """
defaults:
  secrets: [globalKey]
apps:
  demo:
    bundleId: com.demo
    secrets: [appToken]
"""
    )
    eff = resolve(cfg, "demo")
    assert eff.secrets == ["globalKey", "appToken"]


# --- value-based redaction ---


def test_redactor_masks_literal_value_in_text() -> None:
    r = Redactor(None, values=["hunter2"])
    assert r.active
    assert r.redact_text("login with hunter2 ok") == f"login with {PLACEHOLDER} ok"


def test_redactor_masks_value_in_element_tree() -> None:
    r = Redactor(Redact(), values=["s3cr3t"])
    els = [{"identifier": "f", "label": "token: s3cr3t", "value": "s3cr3t", "frame": (0, 0, 1, 1)}]
    out = r.redact_elements(els)  # type: ignore[arg-type]
    assert PLACEHOLDER in out[0]["label"] and out[0]["value"] == PLACEHOLDER


def test_longer_values_masked_first() -> None:
    # "abcd" and "ab" both secret: the longer must not leave a partial "cd".
    r = Redactor(None, values=["ab", "abcd"])
    assert r.redact_text("x abcd y") == f"x {PLACEHOLDER} y"


# --- run-time interpolation ---


def _secret_scenario() -> Scenario:
    return Scenario(
        name="login",
        steps=[Step(type=TypeText(text="${secrets.pw}"))],
    )


def test_interpolation_reaches_the_driver() -> None:
    drv = FakeDriver()
    run_scenario(drv, _secret_scenario(), bindings={"secrets.pw": "hunter2"})
    assert ("type", "hunter2") in drv.actions


def test_scenario_keeps_token_not_value() -> None:
    # The original scenario object (used for the manifest/report) is never mutated.
    scn = _secret_scenario()
    drv = FakeDriver()
    run_scenario(drv, scn, bindings={"secrets.pw": "hunter2"})
    assert scn.steps[0].type is not None and scn.steps[0].type.text == "${secrets.pw}"


def test_no_bindings_passes_token_through_unchanged() -> None:
    drv = FakeDriver()
    run_scenario(drv, _secret_scenario())
    assert ("type", "${secrets.pw}") in drv.actions
