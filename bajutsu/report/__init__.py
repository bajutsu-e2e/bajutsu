"""Reporting — manifest.json, JUnit XML, and a self-contained interactive HTML report.

Split by stage (format -> manifest/richtext -> rows -> panels -> html) so a change to one
stage rarely touches the others (BE-0043). Public API is re-exported here, so
`from bajutsu.report import manifest_dict, junit_xml, html_report, write_report` is unchanged.
"""

from __future__ import annotations

from bajutsu.report.ctrf import ctrf_json
from bajutsu.report.html import (
    html_report,
    scenario_render_inputs,
    write_html_and_junit,
    write_report,
)
from bajutsu.report.load import load_run, rebake, rerender_html, results_from_manifest
from bajutsu.report.manifest import git_revision, junit_xml, manifest_dict, run_provenance

__all__ = [
    "ctrf_json",
    "git_revision",
    "html_report",
    "junit_xml",
    "load_run",
    "manifest_dict",
    "rebake",
    "rerender_html",
    "results_from_manifest",
    "run_provenance",
    "scenario_render_inputs",
    "write_html_and_junit",
    "write_report",
]
