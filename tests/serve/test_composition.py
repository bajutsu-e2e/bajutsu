"""Tests for composing a (config, scenarios, binary) artifact triple (BE-0268).

`materialize_composition` assembles three independently-uploaded, content-addressed artifacts into
the same self-contained tree the deterministic runner already consumes from a BE-0073 combined
bundle — reusing `validate_bundle_config`'s path-confinement check unmodified, plus a new coherence
check (a config field that needs an artifact the caller didn't supply is rejected). Pure packaging:
no device, no AI, runs on the Linux gate against fixture zips.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from bajutsu.serve.operations.composition import CompositionError, materialize_composition

_SCENARIOS_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
)
_FULL_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios, appPath: ./build/Demo.app }\n"
)
_IPA_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, appPath: ./build/Demo.ipa }\n"
)
_NO_ARTIFACTS_CONFIG = (
    "defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo }\n"
)


def _write(tmp_path: Path, name: str, blob: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _scenarios_zip() -> bytes:
    return _zip({"scenarios/smoke.yaml": b"- name: a\n  steps: []\n"})


def _app_zip() -> bytes:
    return _zip({"Info.plist": b"<plist/>", "Demo": b"\x7fELF"})


def test_composes_config_and_scenarios_only(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    scenarios = _write(tmp_path, "scenarios.zip", _scenarios_zip())
    dest = materialize_composition(
        config,
        scenarios,
        None,
        compositions_dir=tmp_path / "compositions",
        composition_id="triple1",
    )
    assert (dest / "bajutsu.config.yaml").read_bytes() == _SCENARIOS_CONFIG.encode()
    assert (dest / "scenarios" / "smoke.yaml").is_file()


def test_composes_a_single_yaml_scenarios_file_into_the_config_dir(tmp_path: Path) -> None:
    # A scenarios artifact that is one YAML file (not a zip) is written into the directory the config
    # names, with the extension normalized to `.yaml` so the runner's `*.yaml` listing finds it.
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    scenarios = _write(tmp_path, "login.yml", b"- name: login\n  steps: []\n")
    dest = materialize_composition(
        config,
        scenarios,
        None,
        compositions_dir=tmp_path / "compositions",
        composition_id="tyaml",
        scenarios_filename="login.yml",
    )
    assert (dest / "scenarios" / "login.yaml").read_bytes() == b"- name: login\n  steps: []\n"
    assert not (dest / "scenarios" / "login.yml").exists()  # .yml normalized to .yaml


def test_single_yaml_scenarios_falls_back_to_a_default_name(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    scenarios = _write(tmp_path, "s.yaml", b"- name: a\n  steps: []\n")
    dest = materialize_composition(
        config,
        scenarios,
        None,
        compositions_dir=tmp_path / "compositions",
        composition_id="tyaml2",
        scenarios_filename=None,
    )
    assert (dest / "scenarios" / "scenario.yaml").is_file()


def test_single_yaml_scenarios_name_cannot_escape_the_scenarios_dir(tmp_path: Path) -> None:
    # A hostile scenariosName is reduced to a safe leaf `*.yaml` — no separator/traversal survives,
    # so the file always lands inside the config's scenarios dir, never outside the composed tree.
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    scenarios = _write(tmp_path, "s.yaml", b"- name: a\n  steps: []\n")
    dest = materialize_composition(
        config,
        scenarios,
        None,
        compositions_dir=tmp_path / "compositions",
        composition_id="ttrav",
        scenarios_filename="../../etc/evil.yml",
    )
    assert [p.name for p in (dest / "scenarios").glob("*.yaml")] == ["evil.yaml"]
    assert not (tmp_path / "etc").exists()  # the `../../` never took effect


def test_composes_full_triple_with_app_bundle_binary(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _FULL_CONFIG.encode())
    scenarios = _write(tmp_path, "scenarios.zip", _scenarios_zip())
    binary = _write(tmp_path, "binary.zip", _app_zip())
    dest = materialize_composition(
        config,
        scenarios,
        binary,
        compositions_dir=tmp_path / "compositions",
        composition_id="triple2",
    )
    assert (dest / "build" / "Demo.app" / "Info.plist").is_file()  # unzipped in place
    assert (dest / "build" / "Demo.app" / "Demo").is_file()


def test_composes_with_raw_ipa_binary_written_as_is(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _IPA_CONFIG.encode())
    binary = _write(tmp_path, "binary.ipa", b"not-a-zip-just-raw-ipa-bytes")
    dest = materialize_composition(
        config,
        None,
        binary,
        compositions_dir=tmp_path / "compositions",
        composition_id="triple3",
    )
    assert (dest / "build" / "Demo.ipa").read_bytes() == b"not-a-zip-just-raw-ipa-bytes"


def test_rejects_when_scenarios_needed_but_not_supplied(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    with pytest.raises(CompositionError, match="scenarios artifact"):
        materialize_composition(
            config, None, None, compositions_dir=tmp_path / "compositions", composition_id="t4"
        )


def test_rejects_when_binary_needed_but_not_supplied(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _FULL_CONFIG.encode())
    scenarios = _write(tmp_path, "scenarios.zip", _scenarios_zip())
    with pytest.raises(CompositionError, match="binary artifact"):
        materialize_composition(
            config,
            scenarios,
            None,
            compositions_dir=tmp_path / "compositions",
            composition_id="t5",
        )


def test_supplied_but_unused_artifact_is_not_an_error(tmp_path: Path) -> None:
    # A config that needs neither scenarios nor a binary still accepts both being supplied — a
    # caller may reuse one artifact against several configs that don't all need it.
    config = _write(tmp_path, "config.yaml", _NO_ARTIFACTS_CONFIG.encode())
    scenarios = _write(tmp_path, "scenarios.zip", _scenarios_zip())
    binary = _write(tmp_path, "binary.bin", b"unused bytes")
    dest = materialize_composition(
        config,
        scenarios,
        binary,
        compositions_dir=tmp_path / "compositions",
        composition_id="t6",
    )
    assert (dest / "bajutsu.config.yaml").is_file()


def test_wrapped_folder_scenarios_zip_is_rejected_not_silently_partial(tmp_path: Path) -> None:
    # The scenarios artifact has no "one level down" tolerance (unlike a legacy combined bundle): its
    # entries must already be relative to the tree root. A zip that wraps everything in an extra
    # folder lands at the wrong place and surfaces as "scenarios path not found", not a silent
    # partial extraction.
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    wrapped = _write(
        tmp_path, "scenarios.zip", _zip({"my-suite/scenarios/smoke.yaml": b"- name: a\n"})
    )
    with pytest.raises(CompositionError, match="was not found after extraction"):
        materialize_composition(
            config, wrapped, None, compositions_dir=tmp_path / "compositions", composition_id="t7"
        )


def test_scenarios_zip_cannot_clobber_the_trusted_config(tmp_path: Path) -> None:
    # Regression: `extract_bundle` only guards zip-slip/zip-bombs, not a top-level entry that happens
    # to be named `bajutsu.config.yaml`. The scenarios artifact must never be able to overwrite the
    # config the caller actually uploaded as the `config` artifact — the config is written last.
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    malicious_scenarios = _write(
        tmp_path,
        "scenarios.zip",
        _zip(
            {
                "scenarios/smoke.yaml": b"- name: a\n  steps: []\n",
                "bajutsu.config.yaml": b"targets:\n  evil: { bundleId: com.evil.app }\n",
            }
        ),
    )
    dest = materialize_composition(
        config,
        malicious_scenarios,
        None,
        compositions_dir=tmp_path / "compositions",
        composition_id="clobber-attempt",
    )
    assert (dest / "bajutsu.config.yaml").read_bytes() == _SCENARIOS_CONFIG.encode()


def test_composition_is_content_addressed_a_cache_hit_skips_reassembly(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    scenarios = _write(tmp_path, "scenarios.zip", _scenarios_zip())
    compositions_dir = tmp_path / "compositions"
    dest = materialize_composition(
        config, scenarios, None, compositions_dir=compositions_dir, composition_id="stable-id"
    )
    # A bogus config path would raise if actually re-read; a cache hit must never touch it.
    bogus_config = tmp_path / "does-not-exist.yaml"
    dest2 = materialize_composition(
        bogus_config,
        None,
        None,
        compositions_dir=compositions_dir,
        composition_id="stable-id",
    )
    assert dest2 == dest
    assert (dest2 / "scenarios" / "smoke.yaml").is_file()


def test_composition_failure_leaves_no_partial_cache_entry(tmp_path: Path) -> None:
    config = _write(tmp_path, "config.yaml", _SCENARIOS_CONFIG.encode())
    compositions_dir = tmp_path / "compositions"
    with pytest.raises(CompositionError):
        materialize_composition(
            config, None, None, compositions_dir=compositions_dir, composition_id="t8"
        )
    assert list(compositions_dir.iterdir()) == []
