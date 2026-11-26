"""Shared builders for the run import tests (BE-0510): a real run tree and run zips."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path

from bajutsu.common.config import Effective, IosConfig
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import FileSink
from bajutsu.common.report.archive import archive_run_dir
from bajutsu.common.runner import Lease, run_and_report
from bajutsu.common.scenario import Redact, Scenario

RUN_ID = "20261126-101500"


class _CapturingDriver(FakeDriver):
    """A fake that writes the files a device would: a screenshot, and a raw XML dump."""

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def last_raw_source(self) -> base.RawSource:
        return base.RawSource(text="<hierarchy/>", parsed_input="<hierarchy a='1'/>", suffix=".xml")


def real_run(runs_dir: Path, run_id: str = RUN_ID) -> Path:
    """Produce a run tree through the real runner and report writer, with a `rawTree` capture."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    element: base.Element = {
        "identifier": "ok",
        "label": "OK",
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }
    eff = Effective(
        target="demo",
        platform_config=IosConfig(bundle_id="com.example.demo"),
        backend=["fake"],
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        capture=["screenshot.after"],
        redact=Redact(),
    )

    def lease(_eff: Effective, _scenario: Scenario) -> Lease:
        return Lease(
            driver=_CapturingDriver([element]),
            sink=FileSink(runs_dir / run_id),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    scenario = Scenario.model_validate(
        {
            "name": "taps ok",
            "steps": [{"tap": {"id": "ok"}, "capture": ["screenshot.after", "rawTree"]}],
        }
    )
    run_and_report(eff, [scenario], lease, runs_dir, run_id)
    return runs_dir / run_id


def run_zip(run_dir: Path, dest: Path) -> Path:
    """Zip a run directory the way the upload script does: one top-level folder, the run id."""
    dest.write_bytes(archive_run_dir(run_dir))
    return dest


def manifest(**extra: object) -> str:
    return json.dumps({"ok": True, "scenarios": [], **extra})


SCENARIO_YAML = "- name: a\n  steps:\n    - tap: { id: ok }\n"


def zip_files(dest: Path, files: Mapping[str, str | bytes]) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    dest.write_bytes(buf.getvalue())
    return dest


def minimal_files(run_id: str = RUN_ID, **extra: str | bytes) -> dict[str, str | bytes]:
    """The smallest importable run, plus *extra* run-relative entries."""
    files: dict[str, str | bytes] = {
        f"{run_id}/manifest.json": manifest(),
        f"{run_id}/scenario.yaml": SCENARIO_YAML,
    }
    files.update({f"{run_id}/{name}": data for name, data in extra.items()})
    return files
