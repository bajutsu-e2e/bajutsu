"""Compose a `(config, scenarios, binary)` artifact triple into one materialized tree (BE-0268).

The runner needs a single self-contained tree its config's relative `scenarios`/`appPath`/
`baselines`/`setup` resolve against — exactly what BE-0073's combined bundle already gives it.
This module is the sibling that assembles the same shape from three independently-uploaded,
content-addressed artifacts instead of one zip: the `config` artifact's bytes become
`bajutsu.config.yaml` at the tree root, the `scenarios` artifact is unzipped directly at that root
(so its entries must already be relative to the root — `scenarios/…`, `baselines/…`, `setup/…` —
with no extra wrapping folder, a stricter contract than the legacy bundle's "one level down"
tolerance), and the `binary` artifact is placed at each target's resolved `appPath` — extracted as
a directory when the config declares a `.app` bundle, written as a raw file otherwise (e.g. an
`.ipa`). `validate_bundle_config` (BE-0073/BE-0051's path-confinement + loadable-config check) is
reused unmodified; the new check here is *coherence*: a config field that needs an artifact the
caller didn't supply is rejected before the tree is ever handed to a run, never silently
half-filled (directive 2)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from bajutsu.config import WebConfig, load_config, resolve
from bajutsu.serve.uploads import extract_bundle, validate_bundle_config

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


def _place_binaries_and_check_coherence(
    root: Path, scenarios_path: Path | None, binary_path: Path | None
) -> None:
    """Place the `binary` artifact at each target's resolved `appPath`, and reject a triple whose
    config needs an artifact the caller didn't supply — or whose supplied artifact didn't actually
    land where the config expects. One `load_config` + one pass over `cfg.targets` does both: a
    target's resolved `scenarios` or `appPath` (iOS/Android) being set requires the matching
    artifact *and* that path must exist on disk after extraction/placement — catching, for
    instance, a `scenarios` zip wrapped in an extra top-level folder (this artifact has no "one
    level down" tolerance, unlike a legacy combined bundle) with a clear error instead of a silent
    partial tree. A supplied-but-unused artifact (e.g. a `binary` uploaded for a scenarios-only
    config) is not an error — a caller may compose one artifact against several configs that don't
    all need it."""
    config_path = root / "bajutsu.config.yaml"
    cfg = load_config(config_path.read_text(encoding="utf-8"))
    placed: set[Path] = set()
    for name in cfg.targets:
        eff = resolve(cfg, name).rebased(root)
        if eff.scenarios is not None:
            if scenarios_path is None:
                raise CompositionError(f"target {name!r} needs a scenarios artifact")
            if not Path(eff.scenarios).is_dir():
                raise CompositionError(
                    f"target {name!r}'s scenarios path {eff.scenarios!r} was not found after "
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
) -> Path:
    """Assemble a `(config, scenarios, binary)` triple into a fresh, content-addressed tree under
    *compositions_dir*, keyed by *composition_id* (a caller-computed digest of the triple) — a
    cache hit returns the existing tree with no re-assembly, mirroring `materialize_bundle`'s trust
    boundary: this replica already proved this exact triple once.

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
    try:
        # Extract scenarios *before* writing the config: a scenarios zip is untrusted content, and
        # `extract_bundle` only guards against zip-slip/zip-bombs, not a top-level entry that
        # happens to be named `bajutsu.config.yaml`. Writing the trusted config bytes last means
        # such an entry is silently overwritten by the real config, never the other way around —
        # composition never binds/validates a config the caller didn't upload as the `config`
        # artifact.
        if scenarios_path is not None:
            extract_bundle(scenarios_path, tmp)
        (tmp / "bajutsu.config.yaml").write_bytes(config_path.read_bytes())
        _place_binaries_and_check_coherence(tmp, scenarios_path, binary_path)
        # Defense-in-depth: `_place_binaries_and_check_coherence` already rebased every target
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
