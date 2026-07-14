"""JSON (de)serialization for `Action` and `ScreenMap` (BE-0257).

Split from the crawl engine (`core`) into its own module: the (de)serialization is a
self-contained concern — turning a crawl's `ScreenMap`/`Action` into a JSON-friendly dict and
back — distinct from the exploration loop. Persisting a map lets a resume continue exploring a
pruned branch (`screenmap_from_dict`) and lets a replayable path be reconstructed
(`action_from_dict`).
"""

from __future__ import annotations

from typing import Any

from bajutsu.crawl.core import Action, Alert, Crash, Edge, Node, Pruned, ScreenMap


def action_to_dict(a: Action) -> dict[str, object]:
    """A JSON-friendly dict of an Action, omitting empty fields.

    Lets a replayable path be persisted (in `pruned`) and reconstructed for a resume.
    """
    d: dict[str, object] = {"kind": a.kind}
    if a.target:
        d["target"] = a.target
    if a.label is not None:
        d["label"] = a.label
    if a.index is not None:
        d["index"] = a.index
    if a.value is not None:
        d["value"] = a.value
    if a.fields:
        d["fields"] = [list(f) for f in a.fields]
    if a.point is not None:
        d["point"] = list(a.point)
    return d


def action_from_dict(d: dict[str, Any]) -> Action:
    """Rebuild an Action from `action_to_dict` (tolerant of missing keys)."""
    point = d.get("point")
    return Action(
        kind=str(d.get("kind") or "tap"),
        target=str(d.get("target") or ""),
        label=d.get("label"),
        index=d.get("index"),
        value=d.get("value"),
        fields=tuple((str(f[0]), str(f[1])) for f in (d.get("fields") or [])),
        point=(float(point[0]), float(point[1])) if point else None,
    )


def screenmap_from_dict(data: dict[str, Any]) -> ScreenMap:
    """Rebuild a ScreenMap from `screenmap_dict` output.

    Lets a saved map be loaded as the base for a resume (continue exploring a pruned branch and
    append to it).
    """
    nodes: dict[str, Node] = {}
    for n in data.get("nodes") or []:
        targets = tuple(
            (desc, (float(r[0]), float(r[1]), float(r[2]), float(r[3])))
            for desc, r in (n.get("targets") or {}).items()
        )
        node = Node(
            fingerprint=str(n["fingerprint"]),
            kind=str(n.get("kind") or "id"),
            ids=tuple(n.get("ids") or []),
            actions=tuple(n.get("actions") or []),
            blocked=tuple(n.get("blocked") or []),
            targets=targets,
        )
        nodes[node.fingerprint] = node
    pruned = [
        Pruned(
            str(p["src"]),
            str(p["action"]),
            str(p["key"]),
            str(p["owner"]),
            tuple(action_from_dict(a) for a in (p.get("path") or [])),
        )
        for p in data.get("pruned") or []
    ]
    return ScreenMap(
        nodes=nodes,
        edges=[
            Edge(str(e["src"]), str(e["action"]), str(e["dst"]), tuple(e.get("alert") or []))
            for e in data.get("edges") or []
        ],
        crashes=[
            Crash(
                tuple(c.get("path") or []),
                tuple(action_from_dict(a) for a in (c.get("actions") or [])),
            )
            for c in data.get("crashes") or []
        ],
        alerts=[
            Alert(tuple(a.get("path") or []), tuple(a.get("buttons") or []))
            for a in data.get("alerts") or []
        ],
        plan={str(fp): list(ops) for fp, ops in (data.get("plan") or {}).items()},
        pruned=pruned,
        paths={
            str(fp): tuple(action_from_dict(a) for a in (acts or []))
            for fp, acts in (data.get("paths") or {}).items()
        },
        stop_reason=str(data.get("stop_reason") or ""),
    )


def screenmap_dict(screen_map: ScreenMap) -> dict[str, object]:
    """Serialize a screen map to a JSON-friendly dict (nodes sorted by fingerprint)."""
    return {
        "nodes": [
            {
                "fingerprint": node.fingerprint,
                "kind": node.kind,
                "ids": list(node.ids),
                "actions": list(node.actions),
                "blocked": list(node.blocked),
                "targets": {desc: list(rect) for desc, rect in node.targets},
            }
            for node in sorted(screen_map.nodes.values(), key=lambda n: n.fingerprint)
        ],
        "edges": [
            {"src": e.src, "action": e.action, "dst": e.dst, "alert": list(e.alert)}
            for e in screen_map.edges
        ],
        "crashes": [
            {"path": list(c.path), "actions": [action_to_dict(a) for a in c.actions]}
            for c in screen_map.crashes
        ],
        "alerts": [{"path": list(a.path), "buttons": list(a.buttons)} for a in screen_map.alerts],
        "plan": {fp: list(ops) for fp, ops in sorted(screen_map.plan.items())},
        "paths": {
            fp: [action_to_dict(a) for a in acts] for fp, acts in sorted(screen_map.paths.items())
        },
        "pruned": [
            {
                "src": p.src,
                "action": p.action,
                "key": p.key,
                "owner": p.owner,
                "path": [action_to_dict(a) for a in p.path],
            }
            for p in screen_map.pruned
        ],
        "stop_reason": screen_map.stop_reason,
    }
