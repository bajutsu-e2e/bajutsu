"""Tests for the convention score and the shared screen probe (doctor)."""

from __future__ import annotations

import json

import pytest

from bajutsu.config import load_config, resolve
from bajutsu.doctor import DoctorProbeError, probe_screen, render, score
from bajutsu.drivers import base


def _el(identifier: str | None, traits: list[str], label: str = "x") -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_ready() -> None:
    screen = [
        _el("settings.open", ["button"]),
        _el("settings.reindex", ["button"]),
        _el("search.field", ["searchField"]),
        _el(None, ["staticText"]),  # not actionable -> ignored
    ]
    s = score(screen, ["settings", "search"])
    assert s.grade == "Ready"
    assert s.id_coverage == 1.0
    assert s.actionable == 3


def test_blocked_low_coverage() -> None:
    screen = [_el("settings.open", ["button"]), _el(None, ["button"], "無名")]
    s = score(screen, ["settings"])
    assert s.grade == "Blocked"
    assert s.id_coverage == 0.5
    assert len(s.missing_id) == 1


def test_partial_coverage() -> None:
    screen = [_el(f"settings.b{i}", ["button"]) for i in range(4)] + [_el(None, ["button"])]
    s = score(screen, ["settings"])
    assert s.id_coverage == 0.8
    assert s.grade == "Partial"


def test_blocked_duplicate() -> None:
    screen = [_el("settings.open", ["button"]), _el("settings.open", ["button"])]
    s = score(screen, ["settings"])
    assert s.grade == "Blocked"
    assert s.duplicates == ["settings.open"]


def test_partial_off_namespace() -> None:
    screen = [_el("settings.open", ["button"]), _el("foo.bar", ["button"])]
    s = score(screen, ["settings"])
    assert s.namespace_conformance == 0.5
    assert s.off_namespace == ["foo.bar"]
    assert s.grade == "Partial"


def test_no_actionable_is_blocked() -> None:
    # A screen with nothing actionable can't be "Ready": it's most likely blank, not yet loaded,
    # or the wrong screen — a false-positive doctor must surface, not paper over (BE-0024).
    s = score([_el(None, ["staticText"])], ["settings"])
    assert s.grade == "Blocked"
    assert s.no_actionable is True


def test_empty_screen_is_blocked() -> None:
    s = score([], ["settings"])
    assert s.grade == "Blocked"
    assert s.no_actionable is True


def test_no_actionable_render_points_at_the_likely_cause() -> None:
    s = score([], ["settings"])
    assert "no actionable elements" in render(s)


def test_a_screen_with_actionable_elements_is_not_flagged_no_actionable() -> None:
    s = score([_el("settings.open", ["button"])], ["settings"])
    assert s.no_actionable is False


def test_custom_thresholds_lower_ok() -> None:
    # 4/5 = 0.8 coverage. Default OK is 0.9 so this would be Partial. With ok=0.8 → Ready.
    screen = [_el(f"settings.b{i}", ["button"]) for i in range(4)] + [_el(None, ["button"])]
    s = score(screen, ["settings"], ok_coverage=0.8, fail_coverage=0.7)
    assert s.id_coverage == 0.8
    assert s.grade == "Ready"


def test_custom_thresholds_higher_fail() -> None:
    # 4/5 = 0.8 coverage. Default fail is 0.7, so 0.8 is Partial. With fail=0.85 → Blocked.
    screen = [_el(f"settings.b{i}", ["button"]) for i in range(4)] + [_el(None, ["button"])]
    s = score(screen, ["settings"], ok_coverage=0.9, fail_coverage=0.85)
    assert s.id_coverage == 0.8
    assert s.grade == "Blocked"


def test_default_thresholds_unchanged() -> None:
    # Calling score() without threshold args behaves as before (regression guard).
    screen = [_el(f"settings.b{i}", ["button"]) for i in range(4)] + [_el(None, ["button"])]
    s = score(screen, ["settings"])
    assert s.id_coverage == 0.8
    assert s.grade == "Partial"


def test_render_mentions_grade() -> None:
    s = score([_el("settings.open", ["button"])], ["settings"])
    assert "grade: Ready" in render(s)


def test_web_traits_are_actionable() -> None:
    """Web-mapped traits (textField, textView, switch, slider, tab, cell) must all be
    recognized as actionable by doctor so it scores web pages correctly (BE-0024)."""
    from bajutsu.dom import parse_dom

    web_elements = [
        {
            "identifier": "f.input",
            "role": "input",
            "label": "Name",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.area",
            "role": "textarea",
            "label": "Bio",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.agree",
            "role": "checkbox",
            "label": "Agree",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.slider",
            "role": "slider",
            "label": "Volume",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.tab",
            "role": "tab",
            "label": "Home",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.item",
            "role": "option",
            "label": "Option 1",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
        {
            "identifier": "f.select",
            "role": "select",
            "label": "Country",
            "value": None,
            "disabled": False,
            "selected": False,
            "frame": [0, 0, 10, 10],
        },
    ]
    elements = parse_dom(web_elements)
    s = score(elements, ["f"])
    # All 7 elements should be actionable (they all have traits in ACTIONABLE_TRAITS)
    assert s.actionable == 7
    assert s.with_id == 7
    assert s.grade == "Ready"


def test_android_clickable_trait_is_actionable() -> None:
    """An adb clickable node's `button` trait (BE-0223) must count as actionable to doctor — and a
    crawl tap-candidate — so a tappable Android container is scored and crawled, scoped to
    clickability rather than the widget class (the twin of `test_web_traits_are_actionable`)."""
    from bajutsu import crawl
    from bajutsu.drivers.adb import parse_hierarchy

    # Two same-class `FrameLayout` wrappers with ids: only the clickable one gains the button trait.
    xml = (
        '<hierarchy><node class="android.widget.FrameLayout" resource-id="stable.row.1"'
        ' text="Horse 1" clickable="true" bounds="[0,0][100,100]" />'
        '<node class="android.widget.FrameLayout" resource-id="stable.deco" text=""'
        ' clickable="false" bounds="[0,100][100,200]" /></hierarchy>'
    )
    elements = parse_hierarchy(xml)
    s = score(elements, ["stable"])
    # The clickable wrapper is the sole actionable element (with its id); the non-clickable one of
    # the same class is not actionable, so it never enters the id-coverage denominator.
    assert s.actionable == 1
    assert s.with_id == 1
    row = next(e for e in elements if e["identifier"] == "stable.row.1")
    assert base.Trait.BUTTON in row["traits"]
    assert crawl.TAP_TRAITS & set(row["traits"])


# --- shared screen probe (BE-0199) ---


class _RecordingDriver:
    """A driver whose query() returns one fixed element, recording nothing itself."""

    def query(self) -> list[base.Element]:
        return [_el("probe.ok", ["button"])]


def _booted_json(udid: str) -> str:
    return json.dumps({"devices": {"iOS": [{"state": "Booted", "udid": udid}]}})


def test_probe_screen_fake_backend_never_touches_simctl() -> None:
    # The fake driver needs no device, so the probe must not resolve a udid (which would shell out
    # to xcrun and fail on a host without Xcode). An injected simctl_run that raises proves it.
    def boom(_cmd: list[str], _env: object = None) -> str:
        raise AssertionError("fake backend must not invoke simctl")

    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    assert probe_screen("fake", "booted", eff, simctl_run=boom) == []  # fake's screen starts empty


class _FakeReadEnv:
    """A stand-in XCUITest environment for doctor's short-lived read: records the udid it was built
    for, returns a recording driver from `start`, and notes teardown."""

    def __init__(self, udid: str, torn: list[str]) -> None:
        self._udid = udid
        self._torn = torn

    def start(self, *_a: object, **_k: object) -> _RecordingDriver:
        return _RecordingDriver()

    def teardown(self, *_a: object, **_k: object) -> None:
        self._torn.append(self._udid)


def _patch_read_env(monkeypatch: pytest.MonkeyPatch, built: list[str], torn: list[str]) -> None:
    def fake_env_for(
        actuator: str, udid: str, env_run: object = None, **_k: object
    ) -> _FakeReadEnv:
        built.append(udid)
        return _FakeReadEnv(udid, torn)

    monkeypatch.setattr("bajutsu.platform_lifecycle.read_session.environment_for", fake_env_for)


def test_probe_screen_xcuitest_uses_a_short_lived_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    # Earlier iOS read the tree with no runner (BE-0019); now (BE-0290) doctor brings a
    # short-lived XCUITest runner up, scores its tree, and tears it down.
    built: list[str] = []
    torn: list[str] = []
    _patch_read_env(monkeypatch, built, torn)
    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    elements = probe_screen("xcuitest", "DEV-1", eff)
    assert [e["identifier"] for e in elements] == ["probe.ok"]
    assert built == ["DEV-1"] and torn == ["DEV-1"]  # runner brought up and torn down


def test_probe_screen_routes_udid_resolution_through_injected_simctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "booted" is resolved to a concrete udid via the injected simctl_run — the seam serve uses to
    # stay host-safe and tests use to avoid shelling out — before the short-lived runner is built.
    built: list[str] = []
    torn: list[str] = []
    _patch_read_env(monkeypatch, built, torn)
    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    probe_screen("xcuitest", "booted", eff, simctl_run=lambda _c, _e=None: _booted_json("BOOTED-9"))
    assert built == ["BOOTED-9"]


def test_probe_screen_takes_the_first_udid_of_a_comma_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # doctor scores one screen; a "A,B" parallel-worker list must target only the first device. The
    # Android path reads through `make_driver` at the resolved udid.
    made: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "bajutsu.doctor.make_driver",
        lambda actuator, udid, **kw: made.append((actuator, udid)) or _RecordingDriver(),
    )
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.android.AndroidEnvironment.resolve_device",
        lambda self, udid: udid,
    )
    eff = resolve(load_config("targets: { demo: { bundleId: com.x, package: com.x } }"), "demo")
    probe_screen("adb", "A,B", eff)
    assert made == [("adb", "A")]


def test_probe_screen_web_without_base_url_raises_probe_error() -> None:
    # A web target with no baseUrl is a fixable config error — a typed DoctorProbeError the adapters
    # map to their own surface, never a crash. The config gate normally rejects a baseUrl-less web
    # target, so we resolve a valid one and null the baseUrl to exercise the defensive backstop.
    import dataclasses

    from bajutsu.config import WebConfig

    eff = dataclasses.replace(
        resolve(load_config("targets: { web: { baseUrl: 'http://x' } }"), "web"),
        platform_config=WebConfig(base_url=None),
    )
    with pytest.raises(DoctorProbeError, match="baseUrl"):
        probe_screen("playwright", "booted", eff)
