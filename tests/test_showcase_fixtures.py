"""Validate the showcase fixtures (config + scenarios) against the schema.

Guards the showcase suite — the single iOS fixture (BE-0079) — and the top-level demo menu's
own tour/features scenarios so they stay loadable as the schema evolves; needs no Simulator.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import json_list

from bajutsu import _yaml
from bajutsu.config import AndroidConfig, Config, IosConfig, load_config, resolve
from bajutsu.scenario import load_scenarios

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "demos" / "showcase" / "scenarios"
MENU_DIR = SCENARIO_DIR / "menu"  # the demo menu's tour/features scenarios (non-globbed subdir)
SHOWCASE_CONFIG = ROOT / "demos" / "showcase" / "showcase.config.yaml"
LIVE_CONFIG = ROOT / "demos" / "showcase" / "live" / "showcase.live.config.yaml"
DEMO_CONFIG = ROOT / "demos" / "demo.config.yaml"
DEVICEFARM_CONFIG = ROOT / "demos" / "showcase" / "devicefarm" / "showcase.devicefarm.config.yaml"

# The showcase a11y target's namespaces (SPEC §9); the menu scenarios stay within them.
NAMESPACES = {"stable", "horse", "search", "log", "notice", "perm", "sys", "net"}


def test_showcase_scenarios_parse() -> None:
    files = sorted(SCENARIO_DIR.glob("*.yaml")) + sorted(MENU_DIR.glob("*.yaml"))
    assert files, "expected showcase scenarios"
    for f in files:
        scenarios = load_scenarios(f.read_text(encoding="utf-8"))
        assert scenarios, f"{f.name} has no scenarios"


def test_dedicated_lane_scenarios_carry_their_exclusion_tag() -> None:
    # The bulk iOS lanes (demos/showcase/Makefile run-swiftui / run-uikit / run-flutter) skip these
    # files by tag alone; an untagged scenario silently rejoins a lane that can only fail it. The
    # scenario schema has no file-level `tags`, so the tag is repeated per scenario and nothing but
    # this assertion notices an omission — no CI lane runs those bulk targets.
    for name, tag in (
        ("visual.yaml", "visual"),
        ("network_android.yaml", "android"),
        ("picker_wheel.yaml", "swiftui"),
        ("permission_system_alert.yaml", "systemalert"),
        ("paste_system_alert.yaml", "systemalert"),
    ):
        for s in load_scenarios((SCENARIO_DIR / name).read_text(encoding="utf-8")):
            assert tag in s.tags, f"{name}: {s.name!r} is missing the `{tag}` tag"


def test_showcase_config_resolves() -> None:
    cfg = load_config(SHOWCASE_CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "showcase-swiftui")
    ios = eff.platform_config
    assert isinstance(ios, IosConfig)
    assert ios.bundle_id == "com.bajutsu.showcase.ios.swiftui"
    assert ios.deeplink_scheme == "showcaseswiftui"
    assert set(eff.id_namespaces) == NAMESPACES

    # BE-0231: the smoke lane's target gates readiness on the very element its first `wait` needs
    # (the first Stable row), so `await_ready` can't return early on some other in-namespace node
    # and let the first scenario step race a not-yet-rendered row on a cold-boot CI Simulator. The
    # candidate list mirrors the scenario selector (BE-0221): dotted iOS form first, underscore form
    # second.
    assert eff.ready_when == {"id": ["stable.row.1", "stable_row_1"]}

    # Guard the platform-scoped id rename (com.bajutsu.showcase.<platform>.<toolkit>) on the
    # other two toolkits, not just showcase-swiftui above.
    uikit = resolve(cfg, "showcase-uikit").platform_config
    assert isinstance(uikit, IosConfig)
    assert uikit.bundle_id == "com.bajutsu.showcase.ios.uikit"
    compose = resolve(cfg, "showcase-compose").platform_config
    assert isinstance(compose, AndroidConfig)
    assert compose.package == "com.bajutsu.showcase.android.compose"

    # BE-0314: the bundled twin carries an app-wide `interrupts` handler, surfaced on the resolved
    # config as a config-level default the run prepends to each scenario's own list.
    bundled = resolve(cfg, "showcase-swiftui-bundled")
    assert len(bundled.run_defaults.interrupts) == 1
    assert bundled.run_defaults.interrupts[0].condition.exists is not None


def _assert_anr_interrupt(cfg: Config, name: str, where: str = "showcase.config.yaml") -> None:
    interrupts = resolve(cfg, name).run_defaults.interrupts
    anr = [
        e
        for e in interrupts
        if e.condition.exists is not None and e.condition.exists.sel.id == ["aerr_wait"]
    ]
    label = f"{where}:{name}"
    assert len(anr) == 1, label
    assert len(anr[0].steps) == 1, label
    tap = anr[0].steps[0].tap
    assert tap is not None, label
    assert tap.id == ["aerr_close"], label


def test_showcase_android_targets_have_anr_interrupt() -> None:
    # BE-0314: every Android showcase target carries the same ANR-dialog handler (e.g. "Pixel
    # Launcher isn't responding"), which must condition on `aerr_wait` (unique to the ANR dialog) but
    # tap `aerr_close` (recovers without leaving the process wedged) — not the same id for both, since
    # `aerr_close` alone also matches a genuine app-under-test crash dialog (see showcase.config.yaml's
    # showcase-compose comment). Both are the driver-normalized local id — not the `android:id/`-
    # qualified resource-id — the same id contract every other selector in this file uses
    # (`stable.row.1`/`stable_row_1`). Pinning the exact ids catches both a wrong-prefix regression and
    # a condition/action id mix-up without an emulator; each mistake previously shipped and only
    # failed against a live dialog (PR #1492 review).
    cfg = load_config(SHOWCASE_CONFIG.read_text(encoding="utf-8"))
    android = [
        name
        for name in cfg.targets
        if isinstance(resolve(cfg, name).platform_config, AndroidConfig)
    ]
    assert android, "expected Android showcase targets"
    for name in android:
        _assert_anr_interrupt(cfg, name)

    # The AWS Device Farm config (BE-0235) drives a bare `bajutsu run` on a reserved device with no
    # Makefile-run ANR_QUIET around it — the same uncovered path this handler exists for, not an
    # optional extra — so it must carry the same handler, kept in step with the local target here.
    devicefarm_cfg = load_config(DEVICEFARM_CONFIG.read_text(encoding="utf-8"))
    _assert_anr_interrupt(
        devicefarm_cfg, "showcase-compose", where="showcase.devicefarm.config.yaml"
    )


def test_showcase_live_config_routes_to_the_live_transport() -> None:
    # The BE-0238 live-route example config resolves, and its `appium` provider surfaces the reserved
    # device's endpoint as the run's udid spec — the same WebDriver-URL signal `environment_for` routes
    # on — so the how-to's `bajutsu run … --config …/showcase.live…` invocation stays valid as the
    # schema evolves. The endpoint is a placeholder (no grid on the gate), so this only checks
    # resolution and the run-time capability narrowing, never a live run.
    from bajutsu.backends import capabilities_for, capabilities_for_run
    from bajutsu.common.drivers import base
    from bajutsu.platform_lifecycle.environments.xcuitest_live import is_webdriver_endpoint
    from bajutsu.runner.device_provider import acquire_device

    cfg = load_config(LIVE_CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "showcase-swiftui-live")
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.bundle_id == "com.bajutsu.showcase.ios.swiftui"

    # The provider hands the run the endpoint as its udid spec; that URL is the live-route signal.
    udid_spec = acquire_device(eff, "booted").udid_spec
    assert is_webdriver_endpoint(udid_spec)

    # The narrowing the how-to describes, keyed on that same udid spec: the WebDriver transport drives
    # neither native text selection nor the simctl-backed families, so preflight would skip a scenario
    # needing one.
    dropped = capabilities_for("xcuitest") - capabilities_for_run("xcuitest", eff, udid_spec)
    assert base.Capability.TEXT_SELECTION in dropped
    assert dropped >= base.DEVICE_CONTROL_ALL


def test_demo_menu_config_declares_the_features_secret() -> None:
    # The menu's `features` tour types `${secrets.PASSWORD}`; the demo config must declare it
    # so the literal is masked in run artifacts.
    cfg = load_config(DEMO_CONFIG.read_text(encoding="utf-8"))
    eff = resolve(cfg, "showcase-swiftui")
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.bundle_id == "com.bajutsu.showcase.ios.swiftui"
    assert eff.secrets == ["PASSWORD"]


# Where each showcase target initializes the Log tab's counter. BE-0285's shared extract.yaml taps
# `log.count` a fixed number of times and asserts an absolute value, so its arithmetic only holds while
# every target starts from the same number — a contract SPEC §5.3 states and four independent native
# sources implement. The on-device lanes catch a drift late (a red 10x-billed macOS job), and on the
# iOS UIKit twin not at all: no CI job runs extract.yaml there.
_LOG_COUNT_INITIALIZERS = {
    "android/compose/src/main/java/com/bajutsu/showcase/compose/AppModel.kt": (
        r"var logCount by mutableIntStateOf\((\d+)\)"
    ),
    "android/views/src/main/java/com/bajutsu/showcase/views/LogTab.kt": r"private var count = (\d+)",
    "ios/swiftui/Sources/LogView.swift": r"@State private var count = (\d+)",
    "ios/uikit/Sources/LogController.swift": r"private var count = (\d+)",
}


def test_extract_scenario_counter_arithmetic_holds_on_every_target() -> None:
    showcase = ROOT / "demos" / "showcase"
    for relative, pattern in _LOG_COUNT_INITIALIZERS.items():
        found = re.search(pattern, (showcase / relative).read_text(encoding="utf-8"))
        assert found, f"{relative}: no `log.count` initializer matching {pattern}"
        assert found.group(1) == "1", f"{relative} starts log.count at {found.group(1)}, not 1"

    scenario = _load_yaml(SCENARIO_DIR / "extract.yaml")[0]
    steps = json_list(scenario["steps"])
    taps = sum(1 for step in steps if "log.count" in _selector_ids(step.get("tap")))
    assert taps, "extract.yaml no longer taps log.count"
    assert int(json_list(scenario["expect"])[0]["value"]["equals"]) == 1 + taps


def _selector_ids(selector: object) -> list[str]:
    """The `id` candidates a step's selector lists, as a list whatever form the YAML used."""
    if not isinstance(selector, dict):
        return []
    ids = selector.get("id")
    if isinstance(ids, str):
        return [ids]
    return [i for i in ids or [] if isinstance(i, str)]


def test_menu_scenario_ids_use_declared_namespaces() -> None:
    ids = _collect_ids(MENU_DIR)
    assert ids  # sanity
    off = sorted(i for i in ids if i.split(".", 1)[0] not in NAMESPACES)
    assert not off, f"ids outside declared namespaces: {off}"


def _load_yaml(path: Path) -> list[dict[str, object]]:
    loaded = _yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list), f"{path.name}: expected a list of scenarios"
    return loaded


def _collect_ids(directory: Path) -> set[str]:
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
