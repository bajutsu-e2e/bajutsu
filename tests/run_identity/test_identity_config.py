"""The `subject.sourceRoot` target key (BE-0506): accepted, resolved, and confined like `subject.path`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.config import load_config, resolve
from bajutsu.common.config.effective import AndroidConfig, Effective, IosConfig


def _effective(platform: str, source_root: str) -> Effective:
    text = f"""
targets:
  app:
    platform: {platform}
    subject:
      id: com.example.app
      path: build/App
      sourceRoot: {source_root}
"""
    return resolve(load_config(text), "app")


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_source_root_resolves_against_the_config_root(tmp_path: Path, platform: str) -> None:
    eff = _effective(platform, "src").rebased(tmp_path)
    assert isinstance(eff.platform_config, IosConfig | AndroidConfig)
    assert eff.platform_config.source_root == str(tmp_path / "src")


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_a_confined_source_root_cannot_leave_the_root(tmp_path: Path, platform: str) -> None:
    with pytest.raises(ValueError, match=r"subject\.sourceRoot"):
        _effective(platform, "../elsewhere").rebased(tmp_path)


def test_a_web_target_takes_no_source_root() -> None:
    with pytest.raises(ValueError, match="sourceRoot"):
        _effective("web", "src")
