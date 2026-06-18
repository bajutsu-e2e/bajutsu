"""Reporting — manifest.json, JUnit XML, and a self-contained interactive HTML report.

Split by stage (format -> manifest/richtext -> rows -> panels -> html) so a change to one
stage rarely touches the others (BE-0043). Public API is re-exported here, so
`from bajutsu.report import manifest_dict, junit_xml, html_report, write_report` is unchanged.
"""

from __future__ import annotations

from bajutsu.report.html import html_report, write_report
from bajutsu.report.manifest import junit_xml, manifest_dict

__all__ = ["html_report", "junit_xml", "manifest_dict", "write_report"]
