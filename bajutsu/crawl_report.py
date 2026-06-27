"""Self-contained HTML screen map for a crawl (BE-0038).

The visual, offline counterpart to the web UI's live graph: a pure, deterministic function of a
`ScreenMap` (the crawl's already-captured model) into a single HTML page — screens laid out in BFS
depth columns, transitions drawn as a static inline SVG, each screen linked to its screenshot. No
device, no model, no JavaScript, no external asset, so it opens straight from the run dir. It only
visualizes what the crawl already found; it never influences the (deterministic) exploration.
"""

from __future__ import annotations

import functools
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from bajutsu.crawl import ScreenMap

# Box + grid geometry. The *layout algorithm* is ported from the web UI's layered graph
# (templates/serve.js); these constants are retuned for the static card (a smaller thumbnail, no
# expand button), so they intentionally differ from the live UI's. A card is NW by NH, columns are
# COLW apart (depth), rows ROWH apart (siblings), with a PAD margin.
_NW, _NH, _COLW, _ROWH, _PAD = 176, 200, 240, 230, 24


@dataclass(frozen=True)
class Box:
    """One screen laid out on the grid: its position and the bits the card shows."""

    fp: str
    kind: str
    ids: tuple[str, ...]
    actions: tuple[str, ...]
    x: int
    y: int
    has_shot: bool


@dataclass(frozen=True)
class EdgeLine:
    """One transition drawn as an SVG path, with the alert marker's anchor."""

    d: str  # SVG path data (a bezier from the source card's right edge to the target's left)
    alert: bool  # the transition tapped through an OS prompt the guard dismissed
    mark_x: int
    mark_y: int


@dataclass(frozen=True)
class Layout:
    """The whole screen map placed on a grid: positioned boxes, drawn edges, and the canvas size."""

    boxes: list[Box]
    edges: list[EdgeLine]
    width: int
    height: int


def layout(screen_map: ScreenMap, have_screens: frozenset[str] = frozenset()) -> Layout:
    """Place the screens on a BFS-depth grid and route the transitions. Pure and deterministic.

    Depth is the BFS distance over non-self transitions from a root (a screen nothing leads into,
    falling back to the first); screens at the same depth share a column, stacked in fingerprint
    order. Mirrors the web UI's layered layout so the static map reads the same as the live one.
    """
    fps = sorted(screen_map.nodes)  # fingerprint order -> deterministic within-layer placement
    adj: dict[str, list[str]] = {fp: [] for fp in fps}
    incoming: set[str] = set()
    for e in screen_map.edges:
        if e.src != e.dst and e.src in adj and e.dst in adj:
            adj[e.src].append(e.dst)
            incoming.add(e.dst)

    roots = [fp for fp in fps if fp not in incoming] or fps[:1]
    depth: dict[str, int] = dict.fromkeys(roots, 0)
    queue = deque(roots)
    while queue:
        f = queue.popleft()
        for t in adj[f]:
            if t not in depth:
                depth[t] = depth[f] + 1
                queue.append(t)
    for fp in fps:  # a screen unreachable over non-self edges still gets a column-0 slot
        depth.setdefault(fp, 0)

    layers: dict[int, list[str]] = {}
    for fp in fps:
        layers.setdefault(depth[fp], []).append(fp)

    pos: dict[str, tuple[int, int]] = {}
    max_rows = 1
    for d, layer in layers.items():
        max_rows = max(max_rows, len(layer))
        for i, fp in enumerate(layer):
            pos[fp] = (_PAD + d * _COLW, _PAD + i * _ROWH)

    boxes = [
        Box(
            fp=fp,
            kind=screen_map.nodes[fp].kind,
            ids=screen_map.nodes[fp].ids,
            actions=screen_map.nodes[fp].actions,
            x=pos[fp][0],
            y=pos[fp][1],
            has_shot=fp in have_screens,
        )
        for fp in fps
    ]
    n_layers = max(layers) + 1 if layers else 1
    width = _PAD * 2 + (n_layers - 1) * _COLW + _NW
    height = _PAD * 2 + (max_rows - 1) * _ROWH + _NH
    return Layout(boxes=boxes, edges=_edges(screen_map, pos), width=width, height=height)


def _edges(screen_map: ScreenMap, pos: dict[str, tuple[int, int]]) -> list[EdgeLine]:
    """Route one SVG path per source→target pair (parallel transitions collapse to one line)."""
    # Aggregate to one line per pair, amber if any underlying transition tapped through an alert —
    # the action detail lives on the screen cards, so the graph stays one arrow per pair.
    agg: dict[tuple[str, str], bool] = {}
    for e in screen_map.edges:
        if e.src in pos and e.dst in pos:
            agg[(e.src, e.dst)] = agg.get((e.src, e.dst), False) or bool(e.alert)

    lines: list[EdgeLine] = []
    for (src, dst), alert in agg.items():
        ax, ay = pos[src]
        bx, by = pos[dst]
        if src == dst:  # self-loop: a small lobe off the card's right edge
            x, y = ax + _NW, ay + _NH // 2
            d = f"M{x},{y - 8} C{x + 34},{y - 26} {x + 34},{y + 26} {x},{y + 8}"
            lines.append(EdgeLine(d=d, alert=alert, mark_x=x + 30, mark_y=y))
        else:
            x1, y1 = ax + _NW, ay + _NH // 2
            x2, y2 = bx, by + _NH // 2
            mx = (x1 + x2) // 2
            d = f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"
            lines.append(EdgeLine(d=d, alert=alert, mark_x=mx, mark_y=(y1 + y2) // 2 - 4))
    return lines


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    # autoescape so a stray "<" in an id can never inject markup into the page.
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


def render_html(
    screen_map: ScreenMap, run_id: str = "", have_screens: frozenset[str] = frozenset()
) -> str:
    """A self-contained HTML screen map (inline CSS, no JavaScript, no external asset).

    `have_screens` is the set of fingerprints with a captured screenshot — only those cards link a
    thumbnail (so a missing capture never shows a broken image). Read-only and model-free.
    """
    lay = layout(screen_map, have_screens)
    return (
        _env()
        .get_template("crawl.html.j2")
        .render(
            run_id=run_id,
            lay=lay,
            screens=len(screen_map.nodes),
            transitions=len(screen_map.edges),
            crashes=screen_map.crashes,
            alerts=screen_map.alerts,
            stop_reason=screen_map.stop_reason,
            short=lambda fp: fp[:7],
        )
    )


def write_html(out_dir: Path, screen_map: ScreenMap, run_id: str = "") -> Path:
    """Write `screenmap.html` into the run dir, beside `screenmap.json` and `screens/`.

    Globs `screens/*.png` so each captured screen links its thumbnail by a relative path that
    resolves when the report is opened straight from the run dir. Returns the written path.
    """
    have = frozenset(p.stem for p in (out_dir / "screens").glob("*.png"))
    report = out_dir / "screenmap.html"
    report.write_text(render_html(screen_map, run_id or out_dir.name, have), encoding="utf-8")
    return report
