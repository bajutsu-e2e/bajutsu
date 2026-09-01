"""BE-0393 unit 1: the configuration `serve` is bound to is one frozen value, replaced whole.

The six fields that together *are* the binding used to be six independent mutable attributes that
had to be written together. What these pin is that no bind can leave a half-updated combination
behind — a bundle paired with the previous source's directory, or a local file still carrying the
last bind's owner — because a bind assigns a new value rather than editing fields.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from _shared import project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.state import ConfigBinding
from bajutsu.serve.uploads import Upload

_CONFIG = "defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo }\n"


def _state(tmp_path: Path, **kw: object) -> srv.ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        **kw,  # type: ignore[arg-type]
    )


def _bundle(root: Path, name: str, *, org: str = "default") -> Upload:
    d = root / name
    d.mkdir(parents=True)
    cfg = d / "bajutsu.config.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    return Upload(dir=d, config=cfg, filename=f"{name}.zip", sha256="a" * 64, size=1, org=org)


def test_the_binding_is_frozen() -> None:
    binding = ConfigBinding()
    assert dataclasses.is_dataclass(binding)
    try:
        binding.config = Path("/elsewhere.yaml")  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("the binding must be replaced, not edited field by field")


def test_the_launch_arguments_seed_the_binding(tmp_path: Path) -> None:
    # Constructing a state still takes the launch config's six facts as arguments; they land on the
    # one value rather than on six attributes.
    provenance = {"host": "github.com", "owner": "acme", "repo": "shop", "ref": "main", "sha": "d"}
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        config=tmp_path / "checkout.yaml",
        cwd=tmp_path,
        config_provenance=provenance,
        git_config_from_api=True,
        config_org="acme",
    )

    assert state.binding == ConfigBinding(
        config=tmp_path / "checkout.yaml",
        cwd=tmp_path,
        provenance=provenance,
        git_from_api=True,
        org="acme",
    )
    # serve's launch directory is captured from the same seed, before any bind can repoint it.
    assert state.base_cwd == tmp_path


def test_binding_a_bundle_states_its_owner_without_a_second_call(tmp_path: Path) -> None:
    # The owner used to be stamped by each caller after `bind_upload`; a caller that forgot left the
    # previous bind's org partitioning targets (BE-0375). It is read off the bundle now.
    uploads = tmp_path / "uploads"
    state = _state(tmp_path, uploads_dir=uploads)
    up = _bundle(uploads, "u1", org="acme")

    state.bind_upload(up)

    assert state.binding == ConfigBinding(config=up.config, cwd=up.dir, upload=up, org="acme")


def test_a_file_bind_after_a_bundle_leaves_nothing_of_the_bundle(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    state = _state(tmp_path, uploads_dir=uploads)
    state.bind_upload(_bundle(uploads, "u1", org="acme"))
    picked = tmp_path / "next.yaml"
    picked.write_text(_CONFIG, encoding="utf-8")

    assert ops.bind_config(state, "next.yaml")[1] == 200

    binding = state.binding
    assert binding.config == picked
    assert binding.cwd == picked.resolve().parent
    # Everything the bundle contributed is gone with the replaced value, in one step.
    assert binding.upload is None and binding.org is None
    assert binding.provenance is None and binding.git_from_api is False


def test_targets_read_the_owner_off_the_binding(tmp_path: Path) -> None:
    # `targets_for` is the one place ownership is decided, and it reads the org that bound the
    # configuration from the binding rather than from a tag alongside it.
    cfg = tmp_path / "owned.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n  demo: { bundleId: com.example.demo }\n"
        "orgs:\n  other:\n    targets: [demo]\n",
        encoding="utf-8",
    )
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg, cwd=tmp_path)

    # The launch configuration names no binding org, so its own `orgs:` block partitions.
    assert state.targets_for("other") == ["demo"]
    assert state.targets_for("acme") == []

    # An API-bound configuration belongs to the org that bound it, and its `orgs:` decides nothing.
    state.binding = dataclasses.replace(state.binding, org="acme")
    assert state.targets_for("acme") == ["demo"]
    assert state.targets_for("other") == []
