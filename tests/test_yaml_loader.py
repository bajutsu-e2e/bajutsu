"""Tests for the private YAML loader (bajutsu._yaml)."""

from __future__ import annotations

import yaml
import yaml.resolver

from bajutsu import _yaml


def test_true_false_still_resolve_to_bool() -> None:
    assert _yaml.safe_load("a: true\nb: false\nc: TRUE\nd: False\n") == {
        "a": True,
        "b": False,
        "c": True,
        "d": False,
    }


def test_on_off_yes_no_stay_strings() -> None:
    loaded = _yaml.safe_load("on: a\noff: b\nyes: c\nno: d\n")
    assert list(loaded) == ["on", "off", "yes", "no"]


def test_loader_owns_its_resolver_mapping() -> None:
    # The customisation must land in `_Loader.__dict__`; the mapping it starts from is inherited
    # from `yaml.resolver.Resolver` and shared with every other loader in the process.
    assert (
        _yaml._Loader.yaml_implicit_resolvers is not yaml.resolver.Resolver.yaml_implicit_resolvers
    )


def test_base_loader_keeps_yaml_1_1_bools() -> None:
    # Importing `bajutsu._yaml` (`tests/conftest.py` pulls in `bajutsu` for every test) must not
    # change what a plain `yaml.safe_load` elsewhere in the process returns — a leak here turns
    # `x is True` assertions into permanently-passing dead code.
    assert yaml.safe_load("a: true\nb: false\nc: on\nd: yes\n") == {
        "a": True,
        "b": False,
        "c": True,
        "d": True,
    }
