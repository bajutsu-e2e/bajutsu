"""Drop-in theme discovery and the serve `ui.default_theme` reader (BE-0191 unit 2)."""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve.themes import (
    BUILTIN_THEMES,
    ThemeManifest,
    discover_themes,
    read_default_theme,
    theme_manifests,
)


def test_no_dir_discovers_nothing() -> None:
    assert discover_themes(None) == []


def test_missing_dir_discovers_nothing(tmp_path: Path) -> None:
    assert discover_themes(tmp_path / "does-not-exist") == []


def test_discovers_a_theme_with_manifest(tmp_path: Path) -> None:
    (tmp_path / "solarized.css").write_text(
        "/* bajutsu-theme\n   name: Solarized Dark\n   kind: dark */\n"
        '[data-theme="solarized"]{--bg:#002b36;--fg:#839496}\n',
        encoding="utf-8",
    )
    found = discover_themes(tmp_path)
    assert len(found) == 1
    assert found[0].manifest == ThemeManifest(id="solarized", name="Solarized Dark", kind="dark")
    assert '[data-theme="solarized"]' in found[0].css


def test_id_comes_from_filename_and_order_is_stable(tmp_path: Path) -> None:
    for stem in ("zeta", "alpha", "mid"):
        (tmp_path / f"{stem}.css").write_text(
            f'/* bajutsu-theme name: {stem} kind: light */\n[data-theme="{stem}"]{{--bg:#fff}}\n',
            encoding="utf-8",
        )
    assert [t.manifest.id for t in discover_themes(tmp_path)] == ["alpha", "mid", "zeta"]


def test_malformed_manifest_falls_back_but_is_not_dropped(tmp_path: Path) -> None:
    # A theme with no manifest comment still registers — dropping it would hide the operator's file.
    (tmp_path / "bare.css").write_text('[data-theme="bare"]{--bg:#111}\n', encoding="utf-8")
    # An unknown kind degrades to the dark default rather than being rejected.
    (tmp_path / "odd.css").write_text(
        '/* bajutsu-theme name: Odd One kind: neon */\n[data-theme="odd"]{--bg:#222}\n',
        encoding="utf-8",
    )
    by_id = {t.manifest.id: t.manifest for t in discover_themes(tmp_path)}
    assert by_id["bare"] == ThemeManifest(id="bare", name="bare", kind="dark")
    assert by_id["odd"] == ThemeManifest(id="odd", name="Odd One", kind="dark")


def test_theme_manifests_prepends_builtins(tmp_path: Path) -> None:
    (tmp_path / "solarized.css").write_text(
        '/* bajutsu-theme name: Solarized kind: dark */\n[data-theme="solarized"]{--bg:#002b36}\n',
        encoding="utf-8",
    )
    manifests = theme_manifests(tmp_path)
    assert manifests[: len(BUILTIN_THEMES)] == list(BUILTIN_THEMES)
    assert manifests[-1].id == "solarized"


def test_unreadable_css_is_skipped_not_fatal(tmp_path: Path) -> None:
    # A non-UTF-8 (or unreadable) *.css is an operator mistake, but it must not crash the scan and
    # take the whole index down — it is skipped while the readable themes still register.
    (tmp_path / "good.css").write_text('[data-theme="good"]{--bg:#111}\n', encoding="utf-8")
    (tmp_path / "bad.css").write_bytes(b"\xff\xfe not valid utf-8 \x00")
    assert [t.manifest.id for t in discover_themes(tmp_path)] == ["good"]


def test_read_default_theme(tmp_path: Path) -> None:
    assert read_default_theme(None) is None
    plain = tmp_path / "plain.yaml"
    plain.write_text("targets:\n  demo:\n    baseUrl: https://example.test\n", encoding="utf-8")
    assert read_default_theme(plain) is None
    themed = tmp_path / "themed.yaml"
    themed.write_text("ui:\n  default_theme: daylight\n", encoding="utf-8")
    assert read_default_theme(themed) == "daylight"


def test_malformed_config_yields_no_default(tmp_path: Path) -> None:
    # A broken config must not turn the theme-default lookup into a startup traceback; the real
    # config loader surfaces the error on its own path.
    bad = tmp_path / "bad.yaml"
    bad.write_text("ui: {default_theme: x\n", encoding="utf-8")  # unbalanced brace → YAMLError
    assert read_default_theme(bad) is None
