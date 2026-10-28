"""Expand a target's `extends` into the ordered layers the defaults fold applies (BE-0471)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .hooks import STEP_CONTAINERS
from .target_config import TARGET_KEYS

# A template takes any target key, none required, plus the `extends` that chains it to others.
TEMPLATE_KEYS: tuple[str, ...] = (*TARGET_KEYS, "extends")

# The path values a template file holds relative to its own directory, rewritten to the config
# file's so everything after the merge sees one base. Kept in step with `Effective.rebased`, which
# a test checks; `services.*` paths do not resolve against the config file today, so they stay.
REBASED_PATHS: tuple[tuple[str, ...], ...] = (
    ("app", "path"),
    ("runsOn", "seedPhotos"),
    ("driver", "runner", "testRunner"),
    ("paths", "scenarios"),
    ("paths", "baselines"),
    ("paths", "schemas"),
    ("paths", "goldens"),
)


class _Expansion:
    """One target's walk over its templates: depth-first, bases first, each template once."""

    def __init__(
        self, target: str, templates: dict[str, Any], config_path: Path | None, root: Path | None
    ) -> None:
        self.target = target
        self.templates = templates
        self.config_dir = config_path.parent.resolve() if config_path is not None else None
        self.root = root.resolve() if root is not None else self.config_dir
        self.layers: list[dict[str, Any]] = []
        self.platforms: list[tuple[str, str]] = []
        self._done: set[str | Path] = set()

    def fail(self, message: str) -> ValueError:
        return ValueError(f"targets.{self.target}.extends: {message}")

    def visit(
        self,
        entry: object,
        base: Path | None,
        chain: tuple[tuple[str | Path, str], ...],
        *,
        in_file: bool = False,
    ) -> None:
        if not isinstance(entry, str) or not entry:
            raise self.fail(f"an entry must be a template name or a file path, got {entry!r}")
        # Imported here: the scenario loader stays off the config schema's import path at load.
        from bajutsu.common.scenario.models.scenario.component import is_component_file_ref

        key: str | Path
        here: Path | None
        if is_component_file_ref(entry):
            key, block, here = self._read_file(entry, base)
            label = f"template file {entry!r}"
        else:
            if in_file:
                raise self.fail(
                    f"{entry!r} in a template file must be a file path: a template file has no "
                    "`templates:` of its own to look a name up in"
                )
            if entry not in self.templates:
                known = ", ".join(sorted(self.templates)) or "none"
                raise self.fail(f"unknown template {entry!r}; templates: {known}")
            key, block, here = entry, self.templates[entry], self.config_dir
            label = f"template {entry!r}"
        if not isinstance(block, dict):
            raise self.fail(f"{label} must be a mapping of target keys")
        if any(seen == key for seen, _ in chain):
            path = " → ".join((*(shown for _, shown in chain), entry))
            raise self.fail(f"a template cycle: {path}")
        if key in self._done:
            return
        problem = _key_problem(block)
        if problem:
            raise self.fail(f"{label}: {problem}")
        for parent in _entries(block.get("extends"), self.fail):
            self.visit(parent, here, (*chain, (key, entry)), in_file=isinstance(key, Path))
        self._done.add(key)
        layer = {k: v for k, v in block.items() if k != "extends"}
        if isinstance(layer.get("platform"), str):
            self.platforms.append((label, layer["platform"]))
        self.layers.append(layer)

    def _read_file(self, ref: str, base: Path | None) -> tuple[Path, Any, Path]:
        if base is None or self.root is None or self.config_dir is None:
            raise self.fail(
                f"{ref!r} is a template file, which resolves against the config file's directory, "
                "but this config was loaded without its path; name inline templates alone"
            )
        from bajutsu.common import _yaml
        from bajutsu.common.scenario.load_expanded import contained_ref, parse_yaml_named

        try:
            path = contained_ref(self.root, base, ref)
            block = parse_yaml_named(path, _yaml.safe_load)
        except (OSError, UnicodeDecodeError) as e:
            raise self.fail(f"cannot read template file {ref!r}: {e}") from e
        except ValueError as e:
            raise self.fail(str(e)) from e
        if isinstance(block, dict):
            block = _rebased(block, path.parent, self.config_dir)
        return path, block, path.parent


def _key_problem(block: dict[str, Any]) -> str | None:
    unknown = [k for k in block if k not in TEMPLATE_KEYS]
    if not unknown:
        return None
    return (
        f"unknown field(s) {', '.join(map(repr, unknown))}; a template accepts "
        f"{', '.join(TEMPLATE_KEYS)}"
    )


def check_templates(templates: object) -> None:
    """Fail on an inline template that is not a mapping or holds a key no target takes.

    Run on every entry under `templates:`, extended or not, so a typo in a template no target uses
    yet fails at load rather than on the day a target first extends it. Nested fields are still
    checked through the targets that extend the template.

    Raises:
        ValueError: The first malformed entry, named.
    """
    if not isinstance(templates, dict):
        return
    for name, block in templates.items():
        if not isinstance(block, dict):
            raise ValueError(  # noqa: TRY004 - Pydantic turns only ValueError into a field error
                f"templates.{name}: a template must be a mapping of target keys"
            )
        problem = _key_problem(block)
        if problem:
            raise ValueError(f"templates.{name}: {problem}")


def _entries(value: object, fail: Callable[[str], ValueError]) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    raise fail(f"must be a template name or a list of them, got {value!r}")


def _rebased(block: dict[str, Any], here: Path, config_dir: Path) -> dict[str, Any]:
    """A template file's *block* with its relative paths rewritten from *here* to *config_dir*."""

    def rebase(value: object) -> object:
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            return value
        return os.path.relpath(here / value, config_dir)

    out: dict[str, Any] = _copy(block)
    for path in REBASED_PATHS:
        *parents, leaf = path
        holder: Any = out
        for key in parents:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if not isinstance(holder, dict) or leaf not in holder:
            continue
        value = holder[leaf]
        holder[leaf] = [rebase(v) for v in value] if isinstance(value, list) else rebase(value)
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        lists = [hooks.get("setup")]
        lists += [r.get("steps") for r in _dicts(hooks.get("cleanup"))]
        lists += [e.get("steps") for e in _dicts(hooks.get("interrupts"))]
        for steps in lists:
            _rebase_uses(steps, rebase)
    return out


def _rebase_uses(steps: object, rebase: Callable[[object], object]) -> None:
    # Imported here for the same reason as in `_Expansion.visit`.
    from bajutsu.common.scenario.models.scenario.component import is_component_file_ref

    for step in _dicts(steps):
        use = step.get("use")
        if isinstance(use, dict):
            ref = use.get("component")
            if isinstance(ref, str) and is_component_file_ref(ref):
                # `relpath` can drop the `/` that marked `../comp` as a file, leaving a bare `comp`
                # that `use` would look up as a name; `./` keeps it a path.
                new = str(rebase(ref))
                use["component"] = new if is_component_file_ref(new) else f"./{new}"
        for _, keys in STEP_CONTAINERS:
            nested: Any = step
            for key in keys:
                nested = nested.get(key) if isinstance(nested, dict) else None
            _rebase_uses(nested, rebase)


def _dicts(value: object) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _copy(value: Any) -> Any:
    # The rewrite edits in place, so it works on a copy of the containers and never the parsed file.
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def expand_extends(
    target: str,
    raw: dict[str, Any],
    templates: object,
    *,
    config_path: Path | None,
    root: Path | None,
) -> list[dict[str, Any]]:
    """The layers *raw* folds onto `defaults`: its templates, bases first, then *raw* itself.

    Each template is folded once, at its first position in the depth-first order, so a base is
    always weaker than every template that extends it. The returned layers carry no `extends`.

    Args:
        target: The target's name, which every error names.
        raw: The target's block as written.
        templates: The config's `templates:` map; anything but a mapping counts as empty and is left
            for the schema to reject.
        config_path: The config file, which inline templates and a target's file references resolve
            against. None for a document with no file, which may name inline templates alone.
        root: The suite root a template file must stay inside (BE-0174); None means the config
            file's own directory.

    Raises:
        ValueError: An unknown template, a cycle, an unreadable or escaping file, an unknown key in
            a template, or layers that declare different platforms.
    """
    walk = _Expansion(
        target,
        templates if isinstance(templates, dict) else {},
        config_path,
        root,
    )
    base = walk.config_dir
    for entry in _entries(raw.get("extends"), walk.fail):
        walk.visit(entry, base, ())
    own = {k: v for k, v in raw.items() if k != "extends"}
    if isinstance(own.get("platform"), str):
        walk.platforms.append((f"target {target!r}", own["platform"]))
    # The target's own key does not override: `platform` selects the shape of `app`, `runsOn`, and
    # `driver`, so a template's iOS-shaped blocks must never reach a web target.
    for label, platform in walk.platforms[1:]:
        first_label, first = walk.platforms[0]
        if platform != first:
            raise ValueError(
                f"targets.{target}: {first_label} declares platform {first!r} but {label} "
                f"declares {platform!r}; every layer that declares `platform` must agree"
            )
    return [*walk.layers, own]
