"""Redaction: masking secrets in evidence (text logs + element trees)."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu import intervals
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink
from bajutsu.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import Redact


def _r(**kw: list[str]) -> Redactor:
    return Redactor(Redact(**kw))


def test_redact_text_masks_known_keys() -> None:
    red = _r(headers=["Authorization", "Cookie"], fields=["token", "password"])
    out = red.redact_text(
        "Authorization: Bearer abc.def\n"
        'POST {"token":"s3cret","keep":"ok"}\n'
        "url?password=hunter2&page=2\n"
        "nothing here\n"
    )
    assert "Bearer abc.def" not in out and "s3cret" not in out and "hunter2" not in out
    assert f"Authorization: {PLACEHOLDER}" in out
    assert f'"token":{PLACEHOLDER}' in out
    assert f"password={PLACEHOLDER}" in out
    # untouched: non-secret content and keys
    assert '"keep":"ok"' in out
    assert "page=2" in out and "nothing here" in out


def test_redactor_inactive_when_unconfigured() -> None:
    red = Redactor(Redact())
    assert red.active is False
    assert red.redact_text("token=abc") == "token=abc"  # no-op


def _el(identifier: str, label: str, value: str) -> base.Element:
    return {"identifier": identifier, "label": label, "value": value, "traits": [], "frame": (0, 0, 1, 1)}


def test_redact_elements_masks_labeled_value() -> None:
    red = _r(labels=["Password"], fields=["token"])
    els = red.redact_elements([
        _el("auth.password", "Password", "hunter2"),
        _el("note", "Note", "auth token=xyz here"),
        _el("plain", "Plain", "nothing secret"),
    ])
    assert els[0]["value"] == PLACEHOLDER           # masked by label
    assert "xyz" not in (els[1]["value"] or "")     # embedded secret scrubbed
    assert els[2]["value"] == "nothing secret"      # untouched


def test_filesink_redacts_elements(tmp_path: Path) -> None:
    sink = FileSink(tmp_path / "run", redact=Redact(labels=["Password"]))
    driver = FakeDriver([_el("auth.password", "Password", "hunter2")])
    sink.capture(driver, "00-s/step0", ["elements"])
    data = json.loads((tmp_path / "run" / "00-s" / "step0" / "elements.json").read_text(encoding="utf-8"))
    assert data[0]["value"] == PLACEHOLDER


def test_filesink_redacts_device_log_on_finish(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "00-s").mkdir(parents=True)
    log = run / "00-s" / "device.log"
    log.write_text("Authorization: Bearer abc\ntoken=secret\nnormal line\n", encoding="utf-8")
    sink = FileSink(run, redact=Redact(headers=["Authorization"], fields=["token"]))
    # A stopped interval whose artifact is this file (default _NullProc: stop() == path).
    sink.finish_scenario_intervals("00-s", [intervals.Interval(kind="deviceLog", path=log)])
    out = log.read_text(encoding="utf-8")
    assert "Bearer abc" not in out and "secret" not in out
    assert f"Authorization: {PLACEHOLDER}" in out and f"token={PLACEHOLDER}" in out
    assert "normal line" in out


def test_filesink_no_redact_leaves_files_untouched(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "00-s").mkdir(parents=True)
    log = run / "00-s" / "device.log"
    log.write_text("token=secret\n", encoding="utf-8")
    FileSink(run).finish_scenario_intervals("00-s", [intervals.Interval(kind="deviceLog", path=log)])
    assert log.read_text(encoding="utf-8") == "token=secret\n"  # no redact config -> unchanged
