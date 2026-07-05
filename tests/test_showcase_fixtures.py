"""Validate the showcase fixtures (config + scenarios) against the schema.

Guards the showcase suite — the single iOS fixture (BE-0079) — and the top-level demo menu's
own tour/features scenarios so they stay loadable as the schema evolves; needs no Simulator.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.config import AndroidConfig, IosConfig, load_config, resolve
from bajutsu.scenario import load_scenarios

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "demos" / "showcase" / "scenarios"
MENU_DIR = SCENARIO_DIR / "menu"  # the demo menu's tour/features scenarios (non-globbed subdir)
SHOWCASE_CONFIG = ROOT / "demos" / "showcase" / "showcase.config.yaml"
DEMO_CONFIG = ROOT / "demos" / "demo.config.yaml"

# The showcase a11y target's namespaces (SPEC §9); the menu scenarios stay within them.
NAMESPACES = {"stable", "horse", "search", "log", "notice", "perm", "sys", "net"}


def test_showcase_scenarios_parse() -> None:
    files = sorted(SCENARIO_DIR.glob("*.yaml")) + sorted(MENU_DIR.glob("*.yaml"))
    assert files, "expected showcase scenarios"
    for f in files:
        scenarios = load_scenarios(f.read_text(encoding="utf-8"))
        assert scenarios, f"{f.name} has no scenarios"


def test_showcase_config_resolves() -> None:
    cfg = load_config(SHOWCASE_CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "showcase-swiftui")
    ios = eff.platform_config
    assert isinstance(ios, IosConfig)
    assert ios.bundle_id == "com.bajutsu.showcase.ios.swiftui"
    assert ios.deeplink_scheme == "showcaseswiftui"
    assert set(eff.id_namespaces) == NAMESPACES

    # Guard the platform-scoped id rename (com.bajutsu.showcase.<platform>.<toolkit>) on the
    # other two toolkits, not just showcase-swiftui above.
    uikit = resolve(cfg, "showcase-uikit").platform_config
    assert isinstance(uikit, IosConfig)
    assert uikit.bundle_id == "com.bajutsu.showcase.ios.uikit"
    compose = resolve(cfg, "showcase-compose").platform_config
    assert isinstance(compose, AndroidConfig)
    assert compose.package == "com.bajutsu.showcase.android.compose"


def test_demo_menu_config_declares_the_features_secret() -> None:
    # The menu's `features` tour types `${secrets.PASSWORD}`; the demo config must declare it
    # so the literal is masked in run artifacts.
    cfg = load_config(DEMO_CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "showcase-swiftui")
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.bundle_id == "com.bajutsu.showcase.ios.swiftui"
    assert eff.secrets == ["PASSWORD"]


def test_menu_scenario_ids_use_declared_namespaces() -> None:
    ids = _collect_ids(MENU_DIR)
    assert ids  # sanity
    off = sorted(i for i in ids if i.split(".", 1)[0] not in NAMESPACES)
    assert not off, f"ids outside declared namespaces: {off}"


def _collect_ids(directory: Path) -> set[str]:
    from bajutsu import _yaml

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

    for f in directory.glob("*.yaml"):
        walk(_yaml.safe_load(f.read_text(encoding="utf-8")))
    return ids
