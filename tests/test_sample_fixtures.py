"""Validate the bundled sample fixtures (config + scenarios) against the schema.

Guards the sample app's scenarios and config so they stay loadable as the schema
evolves; it does not require a Simulator.
"""

from __future__ import annotations

from pathlib import Path

from simpilot.config import load_config, resolve
from simpilot.scenario import load_scenarios

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "sample" / "scenarios"
CONFIG = ROOT / "simpilot.config.yaml"


def test_sample_scenarios_parse() -> None:
    files = sorted(SCENARIO_DIR.glob("*.yaml"))
    assert files, "expected sample scenarios"
    for f in files:
        scenarios = load_scenarios(f.read_text(encoding="utf-8"))
        assert scenarios, f"{f.name} has no scenarios"


def test_sample_config_resolves() -> None:
    cfg = load_config(CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "sample")
    assert eff.bundle_id == "com.simpilot.sample"
    assert eff.deeplink_scheme == "simpilotsample"
    assert "auth" in eff.id_namespaces          # reserved namespace, used by the app
    assert eff.launch_env == {"SAMPLE_UITEST": "1"}


def test_scenario_ids_use_declared_namespaces() -> None:
    # Every id in the sample scenarios should sit under a declared namespace.
    cfg = load_config(CONFIG.read_text(encoding="utf-8"))
    namespaces = set(resolve(cfg, "sample").id_namespaces)
    ids = _collect_ids()
    assert ids  # sanity
    off = sorted(i for i in ids if i.split(".", 1)[0] not in namespaces)
    assert not off, f"ids outside declared namespaces: {off}"


def _collect_ids() -> set[str]:
    from simpilot import _yaml

    ids: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("id", "idMatches") and isinstance(value, str):
                    ids.add(value.replace(".*", "").rstrip("."))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for f in SCENARIO_DIR.glob("*.yaml"):
        walk(_yaml.safe_load(f.read_text(encoding="utf-8")))
    return ids
