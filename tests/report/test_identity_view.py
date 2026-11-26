"""How `ctrf.json` and `report.html` show the provenance stamp's build identity (BE-0506)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_ctrf import _validate

from bajutsu.common.report.ctrf import ctrf_json
from bajutsu.common.report.html import html_report, write_report
from bajutsu.common.report.identity_view import identity_lines
from bajutsu.common.report.load import rerender_html

_PROVENANCE: dict[str, object] = {
    "gitRevision": "abc123",
    "toolSource": {"commit": "f" * 40, "source": "checkout", "uncommitted": False},
    "targets": {
        "shop": {
            "app": {
                "hash": "sha256:" + "a" * 64,
                "hashKind": "tree",
                "version": "1.4",
                "buildNumber": "52",
            },
            "appSource": {"commit": "b" * 40, "uncommitted": True},
        },
        "admin": {
            "app": {
                "hash": None,
                "version": None,
                "unavailable": {"hash": "path-missing", "version": "path-missing"},
            },
            "appSource": {
                "commit": "c" * 40,
                "uncommitted": None,
                "unavailable": {"uncommitted": "status-read-failed"},
            },
        },
        "site": {},
    },
}


def _environment(provenance: dict[str, object]) -> Any:
    doc: Any = ctrf_json("20260704-101500", [], provenance=provenance)
    _validate(doc)
    return doc["results"].get("environment")


def test_ctrf_carries_each_targets_identity_and_its_gaps() -> None:
    environment = _environment(_PROVENANCE)
    assert environment["commit"] == "abc123"
    assert environment["extra"]["toolSource"] == {
        "commit": "f" * 40,
        "uncommitted": False,
        "source": "checkout",
    }
    targets = environment["extra"]["targets"]
    assert targets["shop"] == {
        "app": {"hash": "sha256:" + "a" * 64, "version": "1.4"},
        "appSource": {"commit": "b" * 40, "uncommitted": True},
    }
    assert targets["admin"]["app"] == {
        "hash": None,
        "version": None,
        "unavailable": {"hash": "path-missing", "version": "path-missing"},
    }
    assert targets["admin"]["appSource"] == {
        "commit": "c" * 40,
        "uncommitted": None,
        "unavailable": {"uncommitted": "status-read-failed"},
    }
    assert "site" not in targets


def test_ctrf_omits_the_environment_for_a_stamp_with_nothing_to_record() -> None:
    assert _environment({"toolVersion": "1", "targets": {"site": {}}}) is None
    assert _environment({"targets": "not a mapping"}) is None


def test_header_lines_show_version_hash_dirtiness_and_gaps() -> None:
    shop, admin = identity_lines(_PROVENANCE)
    assert shop == {
        "target": "shop",
        "parts": ["version 1.4", "hash " + "a" * 12, "source " + "b" * 12],
        "uncommitted": True,
    }
    assert admin == {
        "target": "admin",
        "parts": [
            "version unknown (path-missing)",
            "hash unknown (path-missing)",
            "source " + "c" * 12,
            "changes unknown (status-read-failed)",
        ],
        "uncommitted": False,
    }


def test_the_report_header_renders_one_line_per_target() -> None:
    html = html_report("run1", [], identity=identity_lines(_PROVENANCE))
    assert html.count('<div class="build">') == 2
    assert '<span class="bdirty">uncommitted</span>' in html
    assert "hash unknown (path-missing)" in html


def test_a_run_without_the_stamp_has_no_build_lines() -> None:
    assert identity_lines(None) == []
    assert '<div class="build">' not in html_report("run1", [])


def test_the_written_and_rerendered_report_both_show_the_stamp(tmp_path: Path) -> None:
    run_dir = tmp_path / "run1"
    write_report(run_dir, "run1", [], provenance=_PROVENANCE)
    baked = (run_dir / "report.html").read_text(encoding="utf-8")
    assert baked.count('<div class="build">') == 2
    ctrf = json.loads((run_dir / "ctrf.json").read_text(encoding="utf-8"))
    assert set(ctrf["results"]["environment"]["extra"]["targets"]) == {"shop", "admin"}
    (run_dir / "scenario.yaml").write_text("scenarios: []\n", encoding="utf-8")
    assert rerender_html(run_dir).count('<div class="build">') == 2
