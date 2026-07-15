"""Compose a `(config, scenarios, binary)` artifact triple into one materialized tree (BE-0268).

The runner needs a single self-contained tree its config's relative `scenarios`/`appPath`/
`baselines`/`setup` resolve against — exactly what BE-0073's combined bundle already gives it.
This module is the sibling that assembles the same shape from three independently-uploaded,
content-addressed artifacts instead of one zip: the `config` artifact's bytes become
`bajutsu.config.yaml` at the tree root, and the `binary` artifact is placed at each target's
resolved `appPath` — extracted as a directory when the config declares a `.app` bundle, written as a
raw file otherwise (an `.ipa`, an Android `.apk`).

The `scenarios` artifact comes in either of two shapes. A **zip** of the scenario subtree is unzipped
directly at the root (so its entries must already be relative to the root — `scenarios/…`,
`baselines/…`, `setup/…` — with no extra wrapping folder, a stricter contract than the legacy
bundle's "one level down" tolerance). A **single YAML file** is instead written into the directory
each target's `scenarios` field names, so dropping one `smoke.yaml` composes without zipping it first;
the two are told apart by content (`zipfile.is_zipfile`), never by filename.

`validate_bundle_config` (BE-0073/BE-0051's path-confinement + loadable-config check) is reused
unmodified; the new check here is *coherence*: a config field that needs an artifact the caller
didn't supply is rejected before the tree is ever handed to a run, never silently half-filled
(directive 2)."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from bajutsu.config import WebConfig, load_config, resolve
from bajutsu.serve.uploads import extract_bundle, validate_bundle_config

# Everything a single-file `scenarios` artifact's basename may NOT contain. The dropped filename is
# untrusted client input, so its stem is filtered down to this allowlist before it is ever joined
# onto a path — a positive character allowlist (not a blocklist), so no path separator, `..`, NUL, or
# other traversal byte can survive into the write path (defense against CodeQL's "uncontrolled data
# in a path expression", and correct regardless).
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

# Suffixes whose `binary` artifact is a zip of a directory bundle to extract in place, rather than
# a single file to write as-is. An explicit allowlist (not "anything else is raw") so a typo'd or
# novel suffix fails loud here — at bind time — instead of silently producing a corrupt non-zip
# file that only fails opaquely later, at install time.
_ZIP_SUFFIXES = frozenset({".app"})


class CompositionError(ValueError):
    """A triple could not be composed into a coherent tree: a config field names an artifact the
    caller didn't supply, or a supplied artifact doesn't extract/place cleanly. A `ValueError`
    subclass so callers' `except ValueError` covers it, mirroring `BundleError`."""


def _place_binary(binary_path: Path, app_path: Path) -> None:
    """Place the `binary` artifact's bytes at *app_path*: unzip into it if the config declares a
    directory bundle (`.app`), otherwise copy the raw bytes as a single file. `shutil.copyfile`
    streams rather than buffering the whole (up to 1 GiB) binary in memory."""
    if app_path.suffix in _ZIP_SUFFIXES:
        app_path.mkdir(parents=True, exist_ok=True)
        extract_bundle(binary_path, app_path)
    else:
        app_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary_path, app_path)


def _safe_scenario_name(name: str | None) -> str:
    """A confined `*.yaml` basename for a single-file `scenarios` artifact dropped into a target's
    scenarios directory. The dropped *name* is untrusted, so its stem is reduced to an allowlist of
    `[A-Za-z0-9._-]` (`_UNSAFE_NAME_CHARS`) — stripping every path separator and traversal byte, so
    the result can only ever be a leaf name inside the directory — then the extension is normalized
    to `.yaml` (the runner's scenario listing globs `*.yaml`, not `*.yml`, so a dropped `login.yml`
    must land as `login.yaml` to be discoverable), falling back to `scenario.yaml` when nothing
    usable remains."""
    stem = _UNSAFE_NAME_CHARS.sub("", Path(name or "").stem)[:100].strip("._-")
    return f"{stem or 'scenario'}.yaml"


def _place_scenarios_and_binaries_and_check_coherence(
    root: Path,
    scenarios_path: Path | None,
    scenarios_is_zip: bool,
    scenarios_filename: str | None,
    binary_path: Path | None,
) -> None:
    """Place the single-file `scenarios` artifact and the `binary` artifact where each target's
    config names them, and reject a triple whose config needs an artifact the caller didn't supply —
    or whose supplied artifact didn't actually land where the config expects. One `load_config` + one
    pass over `cfg.targets` does it: a target's resolved `scenarios` or `appPath` (iOS/Android) being
    set requires the matching artifact *and* that path must exist on disk after extraction/placement.

    A zip `scenarios` artifact was already unzipped at the root by the caller, so here it is only
    checked for existence — catching, for instance, a zip wrapped in an extra top-level folder (this
    artifact has no "one level down" tolerance, unlike a legacy combined bundle). A single-YAML
    `scenarios` artifact is instead written here, into the directory the config names (created if
    absent), since its target isn't known until the config is loaded. `resolve(...).rebased(root)`
    has already confined every path field to *root* (an escaping `scenarios`/`appPath` raises before
    any write), so both placements stay inside the tree. A supplied-but-unused artifact (a `binary`
    uploaded for a scenarios-only config) is not an error — a caller may compose one artifact against
    several configs that don't all need it."""
    config_path = root / "bajutsu.config.yaml"
    cfg = load_config(config_path.read_text(encoding="utf-8"))
    placed: set[Path] = set()
    for name in cfg.targets:
        eff = resolve(cfg, name).rebased(root)
        if eff.evidence_dirs.scenarios is not None:
            if scenarios_path is None:
                raise CompositionError(f"target {name!r} needs a scenarios artifact")
            scenarios_dir = Path(eff.evidence_dirs.scenarios)
            if not scenarios_is_zip:
                # A single scenario file: the config names a directory, so create it and drop the
                # file in under a `*.yaml` name the runner's listing will find.
                scenarios_dir.mkdir(parents=True, exist_ok=True)
                (scenarios_dir / _safe_scenario_name(scenarios_filename)).write_bytes(
                    scenarios_path.read_bytes()
                )
            if not scenarios_dir.is_dir():
                raise CompositionError(
                    f"target {name!r}'s scenarios path {eff.evidence_dirs.scenarios!r} was not found after "
                    "extraction — check the scenarios zip has no wrapping folder"
                )
        if isinstance(eff.platform_config, WebConfig) or eff.platform_config.app_path is None:
            continue
        if binary_path is None:
            raise CompositionError(f"target {name!r} needs a binary artifact")
        target = Path(eff.platform_config.app_path)
        if target not in placed:
            _place_binary(binary_path, target)
            placed.add(target)
        if not target.exists():
            raise CompositionError(
                f"target {name!r}'s appPath {target!r} was not found after placement"
            )


def materialize_composition(
    config_path: Path,
    scenarios_path: Path | None,
    binary_path: Path | None,
    *,
    compositions_dir: Path,
    composition_id: str,
    scenarios_filename: str | None = None,
) -> Path:
    """Assemble a `(config, scenarios, binary)` triple into a fresh, content-addressed tree under
    *compositions_dir*, keyed by *composition_id* (a caller-computed digest of the triple) — a
    cache hit returns the existing tree with no re-assembly, mirroring `materialize_bundle`'s trust
    boundary: this replica already proved this exact triple once.

    A `scenarios` artifact is a zip of the scenario subtree or a single YAML file, told apart by
    content (`zipfile.is_zipfile`): a zip is extracted at the root, a single file is written into the
    directory the config names, under *scenarios_filename* normalized to a `*.yaml` basename (default
    `scenario.yaml`). *scenarios_filename* is ignored for a zip.

    Raises `CompositionError` (a config field names an artifact not supplied), `BundleError` (an
    invalid zip, zip-slip, or a resource bound crossed while extracting `scenarios`/`binary`), or a
    `load_config`/`rebased` failure (`OSError` / `ValueError` / `yaml.YAMLError`). `OSError` and
    `yaml.YAMLError` are not `ValueError` subclasses, so the call site catches the three explicitly
    (`except (OSError, ValueError, yaml.YAMLError)`) rather than a bare `except ValueError`. A
    failure leaves no partial cache entry.
    """
    compositions_dir.mkdir(parents=True, exist_ok=True)
    dest = compositions_dir / composition_id
    if dest.exists():
        return dest
    tmp = Path(tempfile.mkdtemp(dir=compositions_dir, prefix=f".{composition_id}.tmp-"))
    scenarios_is_zip = scenarios_path is not None and zipfile.is_zipfile(scenarios_path)
    try:
        # Extract a zip scenarios artifact *before* writing the config: it is untrusted content, and
        # `extract_bundle` only guards against zip-slip/zip-bombs, not a top-level entry that
        # happens to be named `bajutsu.config.yaml`. Writing the trusted config bytes last means
        # such an entry is silently overwritten by the real config, never the other way around —
        # composition never binds/validates a config the caller didn't upload as the `config`
        # artifact. A single-YAML scenarios artifact carries no such entry; it is placed later, once
        # the config names its directory.
        if scenarios_path is not None and scenarios_is_zip:
            extract_bundle(scenarios_path, tmp)
        (tmp / "bajutsu.config.yaml").write_bytes(config_path.read_bytes())
        _place_scenarios_and_binaries_and_check_coherence(
            tmp, scenarios_path, scenarios_is_zip, scenarios_filename, binary_path
        )
        # Defense-in-depth: `_place_scenarios_and_binaries_and_check_coherence` already rebased every target
        # against `tmp`, so this mostly re-checks the same paths. It stays as the single reused
        # BE-0073/BE-0051 confinement gate every materialize path funnels through — cheap over one
        # freshly written tree, and keeps this path from silently drifting if that helper's checks
        # ever narrow.
        validate_bundle_config(tmp)
        try:
            tmp.rename(dest)
        except OSError:
            # A concurrent call won the rename; its tree is valid (same composition_id), so drop ours.
            if not dest.exists():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


__all__ = ["CompositionError", "materialize_composition"]
