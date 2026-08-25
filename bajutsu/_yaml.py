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


class Loader(yaml.SafeLoader):
    pass


def _restrict_bool_to_true_false() -> None:
    # `yaml_implicit_resolvers` is inherited from `yaml.resolver.Resolver` and shared with every
    # other loader, so mutating it in place would strip bool resolution from `yaml.safe_load`
    # process-wide. Copy it into `Loader.__dict__` first and edit only our own mapping.
    Loader.yaml_implicit_resolvers = {
        char: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG]
        for char, resolvers in Loader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver(  # type: ignore[no-untyped-call]
        _BOOL_TAG,
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )


_restrict_bool_to_true_false()


def safe_load(text: str) -> Any:
    return yaml.load(text, Loader=Loader)


def safe_dump(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
