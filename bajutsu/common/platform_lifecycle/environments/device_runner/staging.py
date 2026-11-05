"""Stage the runner sources into a scratch tree and render its per-user project spec (BE-0456).

A device build never touches the checkout: it copies the runner sources into a scratch directory,
rewrites that copy for one user's signing identity, and builds there, so ``git status`` stays clean.
The copied set is exactly the one ``source_hash`` covers, which is also what ``make runner-source``
ships in the wheel — one list keeps the hashed, shipped, and built trees identical.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from bajutsu.common import _yaml
from bajutsu.common.platform_lifecycle.environments import _bundled_runner

from .errors import DeviceRunnerError
from .signing import SigningConfig

# The runner sources the wheel carries, mirroring the repository layout so `source_hash` over it
# matches the checkout's (`make runner-source` fills it; gitignored, force-included via `artifacts`).
WHEEL_SOURCE_DIR = Path(__file__).resolve().parents[4] / "_runner_source"

PROJECT_SPEC = Path("BajutsuKit/Runner/project.yml")
PROJECT_NAME = "BajutsuRunner"
HOST_TARGET = "BajutsuRunnerHost"
UITESTS_TARGET = "BajutsuRunnerUITests"

_TEST_TARGET_RE = re.compile(r"\.testTarget\s*\(")
_TARGET_NAME_RE = re.compile(r"\.target\s*\(\s*name:\s*\"([^\"]+)\"")


class StagingError(DeviceRunnerError):
    """The runner sources are absent, or their spec no longer has the shape the build rewrites."""


def runner_source_root() -> Path | None:
    """Where this install's runner sources live: the checkout root, the wheel copy, or neither."""
    if _bundled_runner.runner_source_present():
        return _bundled_runner.repo_root()
    if _bundled_runner.runner_source_present(root=WHEEL_SOURCE_DIR):
        return WHEEL_SOURCE_DIR
    return None


def copy_runner_sources(src_root: Path, dest_root: Path) -> None:
    """Copy every path ``source_hash`` covers from *src_root* to the same place under *dest_root*.

    Shared by ``make runner-source`` (checkout → ``bajutsu/_runner_source/``) and the build's own
    staging step, so the two never disagree on what "the runner sources" are.

    Raises:
        StagingError: A listed path is missing from *src_root*.
    """
    for rel in _bundled_runner.HASH_SOURCE_PATHS:
        src = src_root / rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copy2(src, dest)
        elif src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            raise StagingError(f"runner source path is missing: {src}")


def strip_test_targets(manifest: str) -> str:
    """Drop every ``.testTarget(...)`` declaration from a ``Package.swift``.

    The staged tree carries no ``BajutsuKit/Tests`` (the runner never builds them), and Swift Package
    Manager rejects a manifest whose target path is missing. Deriving the staged manifest from the
    committed one, rather than keeping a second copy of its target list, means a target added later
    reaches the device build without anyone remembering to add it twice.

    Raises:
        StagingError: A ``.testTarget(`` has no matching close parenthesis.
    """
    out: list[str] = []
    pos = 0
    while (match := _TEST_TARGET_RE.search(manifest, pos)) is not None:
        end = _closing_paren(manifest, match.end() - 1) + 1
        # Swallow the trailing comma and the rest of the line, so no blank element is left behind.
        trailing = re.match(r"\s*,?[ \t]*\n?", manifest[end:])
        end += trailing.end() if trailing else 0
        # Swallow the indentation that led up to the declaration on its own line.
        line_start = manifest.rfind("\n", 0, match.start()) + 1
        start = line_start if not manifest[line_start : match.start()].strip() else match.start()
        out.append(manifest[pos:start])
        pos = end
    out.append(manifest[pos:])
    return "".join(out)


def _closing_paren(text: str, open_at: int) -> int:
    """Index of the parenthesis that closes the one at *open_at*.

    Skips string literals and comments, so a path such as ``"Tests (iOS)"`` or a parenthesis in a
    comment never shifts the count.
    """
    depth = 0
    i = open_at
    while i < len(text):
        if text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                break
            i = close + 2
            continue
        if text[i] == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return i
        i += 1
    raise StagingError("Package.swift: unbalanced .testTarget( declaration")


def declared_targets(manifest: str) -> set[str]:
    """The names of the non-test ``.target(name: ...)`` declarations in a ``Package.swift``."""
    return set(_TARGET_NAME_RE.findall(manifest))


def render_project_spec(spec: dict[str, Any], signing: SigningConfig, staged_root: Path) -> None:
    """Rewrite a parsed ``project.yml`` in place for *signing*, building against *staged_root*.

    ``xcodebuild`` command-line settings apply to every target alike, so one
    ``PRODUCT_BUNDLE_IDENTIFIER`` would give the host and the test bundle the same identifier; the
    parsed spec takes per-target settings. Loading and overriding the structure (not ``sed``) means a
    layout change fails here loudly instead of producing a silently unsigned build.

    Raises:
        StagingError: The spec lacks the package or either runner target this rewrites.
    """
    try:
        package = spec["packages"]["BajutsuKit"]
        targets = spec["targets"]
        host = targets[HOST_TARGET]
        uitests = targets[UITESTS_TARGET]
    except (KeyError, TypeError) as exc:
        raise StagingError(
            f"{PROJECT_SPEC} no longer has the expected shape: missing {exc}"
        ) from exc
    package["path"] = str(staged_root)

    manual = signing.manual_signing
    common: dict[str, str] = {
        "DEVELOPMENT_TEAM": signing.team_id,
        "CODE_SIGNING_ALLOWED": "YES",
        "CODE_SIGN_STYLE": "Manual" if manual else "Automatic",
    }
    if manual is not None:
        common["CODE_SIGN_IDENTITY"] = manual.identity
    per_target = (
        (host, signing.host_bundle_id, manual.host_profile() if manual else None),
        (uitests, signing.uitests_bundle_id, manual.runner_profile() if manual else None),
    )
    for target, bundle_id, profile in per_target:
        settings = target.setdefault("settings", {}).setdefault("base", {})
        settings.update(common)
        settings["PRODUCT_BUNDLE_IDENTIFIER"] = bundle_id
        if profile is not None:
            settings["PROVISIONING_PROFILE_SPECIFIER"] = profile


def stage(src_root: Path, dest_root: Path, signing: SigningConfig) -> Path:
    """Copy the runner sources to *dest_root* and rewrite the copy for *signing*.

    Returns:
        The staged ``project.yml``, ready for ``xcodegen``.

    Raises:
        StagingError: The sources are incomplete or their spec has an unexpected shape.
    """
    copy_runner_sources(src_root, dest_root)
    manifest = dest_root / "Package.swift"
    manifest.write_text(strip_test_targets(manifest.read_text()))
    spec_path = dest_root / PROJECT_SPEC
    spec = _yaml.safe_load(spec_path.read_text())
    if not isinstance(spec, dict):
        raise StagingError(f"{PROJECT_SPEC} is not a YAML mapping")
    render_project_spec(spec, signing, dest_root)
    spec_path.write_text(_yaml.safe_dump(spec))
    return spec_path
