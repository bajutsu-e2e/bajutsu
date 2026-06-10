"""Generate a real report.html showcasing the in-report element viewer ("tree").

Drives the actual pipeline (run_scenario + FileSink) against the in-memory
FakeDriver — the same path the CLI uses — so each step captures an elements.json,
then writes report.html. The FakeDriver here also writes a tiny placeholder PNG per
screenshot so the lightbox thumbnails render. No Simulator needed.

Run: `uv run python examples/features/run_tree_report.py` (opens the report).
"""

from __future__ import annotations

import base64
import webbrowser
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink
from bajutsu.orchestrator import run_scenario
from bajutsu.report import write_report
from bajutsu.scenario import Scenario, dump_scenarios, scenario_dict

# A 2x2 light-grey PNG — stands in for a real screenshot so the lightbox isn't broken.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP8//8/"
    "AwMDAxMDAwMAGAUDXKJ8w2gAAAAASUVORK5CYII="
)


def _el(
    identifier: str | None, label: str, traits: list[str], value: str | None, frame: base.Frame
) -> base.Element:
    return {"identifier": identifier, "label": label, "traits": traits, "value": value, "frame": frame}


# A varied screen so the element viewer has something interesting to show.
SCREEN: list[base.Element] = [
    _el("home.title", "Welcome back, Akira", ["staticText"], None, (16.0, 64.0, 280.0, 34.0)),
    _el("search.field", "Search", ["textField"], "coffee", (16.0, 110.0, 280.0, 44.0)),
    _el("search.button", "Search", ["button"], None, (300.0, 110.0, 60.0, 44.0)),
    _el("results.list", "Results", ["table"], None, (0.0, 170.0, 375.0, 500.0)),
    _el("results.0.cell", "Blue Bottle Coffee", ["cell"], None, (0.0, 170.0, 375.0, 64.0)),
    _el("results.1.cell", "Verve Coffee Roasters", ["cell"], None, (0.0, 234.0, 375.0, 64.0)),
    _el("filter.button", "Filter", ["button", "notEnabled"], None, (300.0, 64.0, 60.0, 34.0)),
    _el("nav.tab.home", "Home", ["button", "selected"], None, (0.0, 760.0, 187.0, 49.0)),
    _el(None, "decorative divider", [], None, (0.0, 168.0, 375.0, 1.0)),
]


class ShotFakeDriver(FakeDriver):
    """FakeDriver that writes a real (tiny) PNG on screenshot so thumbnails render."""

    def screenshot(self, path: str) -> None:
        super().screenshot(path)
        Path(path).write_bytes(_PNG)


SCENARIO = Scenario.model_validate({
    "name": "search coffee",
    "steps": [
        {"tap": {"id": "search.field"}, "name": "focus the search field"},
        {"tap": {"id": "search.button"}, "name": "run the search"},
        {"tap": {"id": "nav.tab.home"}, "name": "back to home"},
    ],
    "expect": [{"exists": {"id": "home.title"}}],
})


def main() -> None:
    run_id = "demo-tree"
    run_dir = Path("runs") / run_id
    sink = FileSink(run_dir)
    result = run_scenario(ShotFakeDriver(screen=list(SCREEN)), SCENARIO, sink=sink)
    manifest = write_report(
        run_dir, run_id, [result],
        definitions=[scenario_dict(SCENARIO)], sources=[dump_scenarios([SCENARIO])],
    )
    report = manifest.parent / "report.html"
    print(f"[{'PASS' if result.ok else 'FAIL'}] {SCENARIO.name} -> {report}")
    print("Open the report and click a step's “tree” button: the captured elements")
    print("now open in an in-report overlay (filterable), not a new browser tab.")
    webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
