"""YAML loading that keeps on/off/yes/no as strings.

YAML 1.1 (what PyYAML implements) resolves on/off/yes/no to booleans, which would
turn the capturePolicy `on:` trigger key into True. We keep only true/false as
booleans so `on:` stays a string key.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_BOOL_TAG = "tag:yaml.org,2002:bool"


class _Loader(yaml.SafeLoader):
    pass


def _restrict_bool_to_true_false() -> None:
    for char, resolvers in list(_Loader.yaml_implicit_resolvers.items()):
        _Loader.yaml_implicit_resolvers[char] = [
            (tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG
        ]
    _Loader.add_implicit_resolver(  # type: ignore[no-untyped-call]
        _BOOL_TAG,
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )


_restrict_bool_to_true_false()


def safe_load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


def safe_dump(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
