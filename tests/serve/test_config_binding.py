"""BE-0393 unit 1: the configuration `serve` is bound to is one frozen value, replaced whole.

The six fields that together *are* the binding used to be six independent mutable attributes that
had to be written together. What these pin is that no bind can leave a half-updated combination
behind — a bundle paired with the previous source's directory, or a local file still carrying the
last bind's owner — because a bind assigns a new value rather than editing fields.
"""

from __future__ import annotations

import dataclasses
from functools import partial
from pathlib import Path

import pytest
from _shared import fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve import state as srv_state
from bajutsu.serve.operations import config as config_ops
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

    state.bind_upload(up, None)

    assert state.binding == ConfigBinding(config=up.config, cwd=up.dir, upload=up, org="acme")


def test_a_file_bind_after_a_bundle_leaves_nothing_of_the_bundle(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    state = _state(tmp_path, uploads_dir=uploads)
    state.bind_upload(_bundle(uploads, "u1", org="acme"), None)
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


# --- the working directory a job spawns against (BE-0393 unit 2) ---


def test_a_registered_job_keeps_the_directory_it_was_accepted_against(tmp_path: Path) -> None:
    """A rebind between registration and spawn must not repoint a run that was already accepted.
    The `--config` on the command line already froze which configuration the run parses; the
    directory it resolves relative paths against is frozen here."""
    uploads = tmp_path / "uploads"
    state = _state(tmp_path, uploads_dir=uploads)
    at_enqueue = state.binding.cwd

    job = state.register(srv.Job(cmd=["bajutsu", "run"]))
    # The member switches to a bundle while the job sits in the queue.
    state.bind_upload(_bundle(uploads, "u1"), None)

    assert state.binding.cwd != at_enqueue  # the live binding did move
    assert job.cwd == at_enqueue  # the accepted job did not


def test_the_capped_registration_path_freezes_it_too(tmp_path: Path) -> None:
    # `try_register` is the path every dispatcher actually takes; a job accepted under the caps must
    # carry the same frozen directory as one registered directly.
    state = _state(tmp_path)
    job = state.try_register(srv.Job(cmd=["bajutsu", "run"]))
    assert job is not None and job.cwd == state.binding.cwd


def test_an_explicit_working_directory_is_left_alone(tmp_path: Path) -> None:
    # A worker rebuilds a job from its spec and resolves its own workspace; a caller that named a
    # directory keeps it.
    state = _state(tmp_path)
    elsewhere = tmp_path / "workspace"
    job = state.register(srv.Job(cmd=["bajutsu", "run"], cwd=elsewhere))
    assert job.cwd == elsewhere


# --- a binding per session and acting org (BE-0393 unit 2) ---


def _bound(state: srv.ServeState, session: str | None, org: str = "default") -> Path | None:
    return state.binding_for(session, org).config


def test_a_bind_is_visible_to_the_session_that_made_it_and_no_other(tmp_path: Path) -> None:
    """Two members of one org must be able to work at once: one switching configurations cannot move
    the ground under the other's run."""
    state = _state(tmp_path)
    mine = tmp_path / "mine.yaml"
    mine.write_text(_CONFIG, encoding="utf-8")
    launch = state.binding.config

    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200

    assert _bound(state, "s1") == mine
    # A colleague's session, and one that never bound anything, both keep the deployment's.
    assert _bound(state, "s2") == launch
    assert _bound(state, None) == launch
    assert state.binding.config == launch


def test_the_acting_org_is_half_the_key(tmp_path: Path) -> None:
    # A session may change which org it acts as, and target ownership rides on the org — a binding
    # made as one org must not answer as another.
    state = _state(tmp_path)
    mine = tmp_path / "mine.yaml"
    mine.write_text(_CONFIG, encoding="utf-8")
    state.rebind("s1", "acme", ConfigBinding(config=mine, cwd=tmp_path, org="acme"))

    assert _bound(state, "s1", "acme") == mine
    assert _bound(state, "s1", "other") == state.binding.config


def test_a_session_with_no_binding_reads_the_deployments(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert _bound(state, "never-bound") == state.binding.config


def test_a_caller_with_no_session_replaces_the_deployments_binding(tmp_path: Path) -> None:
    # A shared-token or CI request carries no login cookie; the only binding it could mean is the
    # deployment's, which is also the pre-unit-2 behavior.
    state = _state(tmp_path)
    mine = tmp_path / "mine.yaml"
    mine.write_text(_CONFIG, encoding="utf-8")

    assert ops.bind_config(state, "mine.yaml", session=None)[1] == 200

    assert state.binding.config == mine
    assert _bound(state, "any-session") == mine


def test_the_map_evicts_the_least_recently_bound(tmp_path: Path) -> None:
    # A process that accumulates quiet sessions must not grow without limit; eviction costs that
    # session its own binding and nothing else.
    state = _state(tmp_path)
    cfg = tmp_path / "mine.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    for n in range(srv_state.MAX_SESSION_BINDINGS + 1):
        state.rebind(f"s{n}", "default", ConfigBinding(config=cfg, cwd=tmp_path))

    assert len(state.bindings) == srv_state.MAX_SESSION_BINDINGS
    assert ("s0", "default") not in state.bindings  # the oldest went
    assert _bound(state, "s0") == state.binding.config  # and reads the fallback again
    assert _bound(state, "s1") == cfg


def test_revoked_sessions_lose_their_slots(tmp_path: Path) -> None:
    state = _state(tmp_path)
    cfg = tmp_path / "mine.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    live = state.auth.issue_session(identity="kazu")
    state.rebind(live, "default", ConfigBinding(config=cfg, cwd=tmp_path))
    state.rebind("already-gone", "default", ConfigBinding(config=cfg, cwd=tmp_path))

    dropped = state.drop_revoked_bindings()

    assert dropped == 1
    assert ("already-gone", "default") not in state.bindings
    assert _bound(state, live) == cfg


def test_target_ownership_follows_the_asking_sessions_binding(tmp_path: Path) -> None:
    # `targets_for` is the one place ownership is decided, so it has to read the binding the asking
    # session holds — not an ambient one a colleague may have just replaced.
    owned = tmp_path / "owned.yaml"
    owned.write_text(
        "defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo }\n",
        encoding="utf-8",
    )
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=owned, cwd=tmp_path)
    state.rebind("s1", "acme", ConfigBinding(config=owned, cwd=tmp_path, org="acme"))

    assert state.targets_for("acme", "s1") == ["demo"]  # bound as acme, so acme owns it
    assert state.targets_for("acme", "s2") == []  # another session sees the launch partition


def test_a_dispatched_job_runs_against_the_session_that_asked(tmp_path: Path) -> None:
    """The job's `--config` comes from the asking session's binding, so its working directory has to
    come from the same one — otherwise a session-bound bundle's relative `appPath` and `scenarios`
    resolve against the deployment's tree instead of the bundle's."""
    uploads = tmp_path / "uploads"
    state = _state(
        tmp_path, uploads_dir=uploads, popen=fake_popen(["PASS  runs/1/manifest.json\n"])
    )
    bundle = _bundle(uploads, "u1")
    state.bind_upload(bundle, "s1")

    payload, status = ops.start_run(
        state, {"target": "demo", "scenario": "smoke.yaml"}, session="s1"
    )

    assert status == 200, payload
    job = state.jobs[payload["jobId"]]
    assert job.cwd == bundle.dir
    # A session that bound nothing keeps dispatching against the deployment's own binding.
    other, status = ops.start_run(state, {"target": "demo", "scenario": "smoke.yaml"}, session="s2")
    assert status == 200, other
    assert state.jobs[other["jobId"]].cwd == state.binding.cwd


def test_the_cross_org_guard_follows_the_asking_sessions_binding(tmp_path: Path) -> None:
    """Target ownership is a property of the binding, so the guard has to read the one the rest of
    the request is running against — otherwise it answers 403 from a partition the request is not
    using."""
    from bajutsu.serve.operations._common import _resolve_org_or_forbid

    launch = tmp_path / "launch.yaml"
    launch.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n  web: { bundleId: com.example.web }\n"
        "orgs:\n  other:\n    targets: [web]\n",
        encoding="utf-8",
    )
    mine = tmp_path / "mine.yaml"
    mine.write_text(
        "defaults: { backend: [ios] }\ntargets:\n  web: { bundleId: com.example.web }\n",
        encoding="utf-8",
    )

    class _Repo:
        """Enough of the repository seam to switch the guard on; org scoping is off without one."""

        def user_org(self, _actor: str) -> str:
            return "acme"

    state = srv.ServeState(runs_dir=tmp_path / "runs", config=launch, cwd=tmp_path)
    state.repository = _Repo()  # type: ignore[assignment]
    # The launch config awards `web` to `other`, so acme is forbidden it...
    assert _resolve_org_or_forbid(state, "web", "kazu", None)[1] is not None
    # ...until acme binds a config of its own, which it then owns outright (BE-0375).
    state.rebind("s1", "acme", ConfigBinding(config=mine, cwd=tmp_path, org="acme"))
    assert _resolve_org_or_forbid(state, "web", "kazu", "s1")[1] is None


def test_a_capture_remembers_the_session_that_started_it(tmp_path: Path) -> None:
    """The capture drives the config its own session is bound to, so the authored scenario has to
    land in that config's scenarios dir. Holding the id rather than re-reading it at finish also
    freezes the destination against a rebind partway through."""
    from bajutsu.drivers.fake import FakeDriver

    state = _state(tmp_path)

    _payload, status = ops.start_capture(
        state,
        {"target": "demo"},
        session="s1",
        driver_factory=lambda _e, _b, _u: (FakeDriver([]), lambda: None),
    )

    assert status == 200
    assert state.capture is not None and state.capture.login_session == "s1"


def test_restoring_an_orgs_remembered_bundle_lands_in_the_asking_session(tmp_path: Path) -> None:
    """Before unit 2 every bind replaced one process-global binding, so a restore that clobbered it
    was self-healing. Now that normal binds land in session slots, a restore that replaced the
    deployment's fallback would persist there — leaving every other org's sessionless requests
    resolving against this org's bundle until a restart."""
    uploads = tmp_path / "uploads"
    state = _state(tmp_path, uploads_dir=uploads)
    fallback = state.binding.config
    sha = "a" * 64
    # The extraction cache entry the org's record names, already on this replica (BE-0393 unit 5).
    (uploads / sha).mkdir(parents=True)
    (uploads / sha / "bajutsu.config.yaml").write_text(_CONFIG, encoding="utf-8")

    class _Repo:
        def get_org(self, _org: str) -> object:
            return type("Row", (), {"config_source": {"kind": "upload", "sha256": sha}})()

    state.repository = _Repo()  # type: ignore[assignment]

    payload, status = ops.restore_org_config(state, org="default", session="s1")

    assert status == 200, payload
    assert _bound(state, "s1") != fallback  # the asking session got it
    assert state.binding.config == fallback  # the deployment's fallback is untouched
    assert _bound(state, "s2") == fallback  # so another session still reads the launch config


# --- restoring the org's remembered configuration, lazily (BE-0393 unit 6) ---


class _OrgRow:
    def __init__(self, source: object) -> None:
        self.config_source = source


class _MemoryRepo:
    """Enough of the repository seam to hold one org's remembered configuration."""

    def __init__(self, source: object) -> None:
        self.source = source
        self.reads = 0

    def get_org(self, _org: str) -> object:
        self.reads += 1
        return _OrgRow(self.source)

    def user_org(self, _actor: str) -> str:
        return "default"

    def set_org_config_source(self, _org: str, source: object) -> bool:
        self.source = source
        return True


def _with_memory(tmp_path: Path, source: object) -> tuple[srv.ServeState, _MemoryRepo]:
    """A state whose org remembers *source*, with the lazy restore wired as `serve()` wires it."""
    from bajutsu.serve.operations.config import restore_org_binding

    state = _state(tmp_path)
    repo = _MemoryRepo(source)
    state.repository = repo  # type: ignore[assignment]
    state.restore_binding = partial(restore_org_binding, state)
    return state, repo


def test_a_session_inherits_what_its_org_last_bound(tmp_path: Path) -> None:
    """A member who binds nothing starts against the org's configuration rather than the deployment's
    — the whole point of the item: no re-upload, no re-pasted Git spec, no file-browser pick."""
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, _repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})
    fallback = state.binding.config

    assert _bound(state, "s1") == remembered
    assert state.binding.config == fallback  # the deployment's own binding is untouched


def test_the_restore_happens_once_per_session_and_org(tmp_path: Path) -> None:
    # Restored into the slot, so the second read is a lookup rather than a second materialization.
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})

    assert _bound(state, "s1") == remembered
    assert _bound(state, "s1") == remembered

    assert repo.reads == 1


def test_a_failed_restore_leaves_the_fallback_and_is_not_retried(tmp_path: Path) -> None:
    """Best-effort: a moved file, an unreachable repository, or an evicted bundle must not fail the
    request that happened to be first — and must not re-fetch on every request after it."""
    state, repo = _with_memory(
        tmp_path, {"kind": "file", "locator": {"path": str(tmp_path / "gone.yaml")}}
    )
    fallback = state.binding.config

    assert _bound(state, "s1") == fallback
    assert _bound(state, "s1") == fallback

    assert repo.reads == 1


def test_a_restore_that_raises_never_fails_the_request(tmp_path: Path) -> None:
    class _Exploding(_MemoryRepo):
        def get_org(self, _org: str) -> object:
            self.reads += 1
            raise RuntimeError("the database is unreachable")

    from bajutsu.serve.operations.config import restore_org_binding

    state = _state(tmp_path)
    repo = _Exploding(None)
    state.repository = repo  # type: ignore[assignment]
    state.restore_binding = partial(restore_org_binding, state)

    assert _bound(state, "s1") == state.binding.config
    assert repo.reads == 1


def test_a_session_that_bound_something_itself_is_not_overwritten(tmp_path: Path) -> None:
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    mine = tmp_path / "mine.yaml"
    mine.write_text(_CONFIG, encoding="utf-8")
    state, repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})

    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200

    assert _bound(state, "s1") == mine
    # No restore was attempted: the slot was never empty when it was read.
    assert repo.reads == 0


def test_a_caller_with_no_session_restores_nothing(tmp_path: Path) -> None:
    # A shared-token or CI request acts on the deployment; there is no slot for it to inherit into.
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})

    assert _bound(state, None) == state.binding.config
    assert repo.reads == 0


def test_every_bind_records_what_the_org_last_bound(tmp_path: Path) -> None:
    """The memory is what the org last bound, not only what it could re-fetch — BE-0393's Motivation
    names the Git spec and the file-browser pick alongside the upload."""
    recorded: list[object] = []

    class _Recording(_MemoryRepo):
        def set_org_config_source(self, _org: str, source: object) -> bool:
            recorded.append(source)
            return True

    state = _state(tmp_path)
    state.repository = _Recording(None)  # type: ignore[assignment]
    picked = tmp_path / "picked.yaml"
    picked.write_text(_CONFIG, encoding="utf-8")

    assert ops.bind_config(state, "picked.yaml", session="s1")[1] == 200

    assert recorded == [{"kind": "file", "locator": {"path": str(picked)}}]


def test_a_git_source_is_restored_through_the_ordinary_binder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restored configuration is screened exactly as the bind that recorded it was — it goes
    through the same binder rather than being trusted from the record."""
    import bajutsu.serve.operations.config as config_ops

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "web.yaml").write_text(_CONFIG, encoding="utf-8")

    class _Mat:
        root = checkout
        config_path = checkout / "web.yaml"
        sha = "deadbeef"

    monkeypatch.setattr(config_ops, "materialize", lambda _spec: _Mat())
    state, _repo = _with_memory(
        tmp_path,
        {
            "kind": "git",
            "locator": {
                "host": "github.com",
                "owner": "acme",
                "repo": "shop",
                "ref": "main",
                "path": "web.yaml",
            },
        },
    )

    assert _bound(state, "s1") == checkout / "web.yaml"
    restored = state.binding_for("s1", "default")
    # Restored as a runtime API bind, so its `build:` stays untrusted (BE-0121) and the org that
    # bound it owns every target it declares (BE-0375) — both stamped by the binder, not the record.
    assert restored.git_from_api is True
    assert restored.org == "default"


def test_an_uploaded_bundle_is_restored_by_digest(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    sha = "b" * 64
    (uploads / sha).mkdir(parents=True)
    (uploads / sha / "bajutsu.config.yaml").write_text(_CONFIG, encoding="utf-8")
    state, _repo = _with_memory(tmp_path, {"kind": "upload", "sha256": sha})
    state.uploads_dir = uploads

    bound = state.binding_for("s1", "default")

    assert bound.upload is not None and bound.config == uploads / sha / "bajutsu.config.yaml"


def test_a_record_naming_no_spec_restores_nothing(tmp_path: Path) -> None:
    state, repo = _with_memory(tmp_path, {"kind": "elsewhere", "locator": {"path": "/x.yaml"}})
    assert _bound(state, "s1") == state.binding.config
    assert repo.reads == 1


def test_an_org_that_remembers_nothing_restores_nothing(tmp_path: Path) -> None:
    state, repo = _with_memory(tmp_path, None)
    assert _bound(state, "s1") == state.binding.config
    assert repo.reads == 1


def test_the_no_retry_record_is_bounded_like_the_bindings(tmp_path: Path) -> None:
    # A process that never sees its sessions end must not remember every pair it declined to restore
    # for, either. Eviction only costs that pair a second attempt.
    state, repo = _with_memory(tmp_path, None)

    for n in range(srv_state.MAX_SESSION_BINDINGS + 1):
        state.binding_for(f"s{n}", "default")

    assert len(state._restore_tried) == srv_state.MAX_SESSION_BINDINGS
    assert repo.reads == srv_state.MAX_SESSION_BINDINGS + 1
    # The evicted pair is tried once more, and still finds nothing to restore.
    state.binding_for("s0", "default")
    assert repo.reads == srv_state.MAX_SESSION_BINDINGS + 2


def test_a_restore_for_a_non_default_org_stays_in_that_org(tmp_path: Path) -> None:
    """The restore acts as an org it was handed, and the binders' own `org_of(None)` would answer
    `default` — which would bind one org's configuration into another's slot and write its record
    onto that org's row."""
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    written: list[tuple[str, object]] = []

    class _AcmeRepo(_MemoryRepo):
        def user_org(self, _actor: str) -> str:
            return "acme"

        def set_org_config_source(self, org: str, source: object) -> bool:
            written.append((org, source))
            return True

    state = _state(tmp_path)
    repo = _AcmeRepo({"kind": "file", "locator": {"path": str(remembered)}})
    state.repository = repo  # type: ignore[assignment]
    state.restore_binding = partial(config_ops.restore_org_binding, state)

    assert state.binding_for("s1", "acme").config == remembered
    # The slot it landed in is acme's, not `default`'s.
    assert ("s1", "acme") in state.bindings
    assert ("s1", "default") not in state.bindings
    # A restore replays what the row already holds, so it writes nothing back — which is also what
    # keeps a bind that raced this restore's fetch from being reverted.
    assert written == []


def test_a_refused_restore_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # The binders report a refusal as a value, not an exception, so without this the reason the
    # session stayed on the fallback would never surface anywhere.
    state, _repo = _with_memory(
        tmp_path, {"kind": "file", "locator": {"path": str(tmp_path / "gone.yaml")}}
    )

    with caplog.at_level("WARNING"):
        assert _bound(state, "s1") == state.binding.config

    assert any("could not restore org" in r.getMessage() for r in caplog.records)


def test_an_unresolvable_bundle_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # The bundle path answers "no longer resolvable" with None rather than an error, so without this
    # the reason an upload-remembering org stayed on the fallback would surface nowhere.
    state, _repo = _with_memory(tmp_path, {"kind": "upload", "sha256": "c" * 64})

    with caplog.at_level("WARNING"):
        assert _bound(state, "s1") == state.binding.config

    assert any("no longer resolvable" in r.getMessage() for r in caplog.records)


def test_a_store_error_restoring_a_bundle_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Distinct from "no longer resolvable": the bytes may well exist, the store just could not be
    # reached. A transient infrastructure failure must not read as an absent bundle.
    from _shared import FakeObjectStore

    class _Unreachable(FakeObjectStore):
        def get_bytes(self, key: str) -> bytes | None:
            raise ConnectionError("bucket unreachable")

    state, _repo = _with_memory(tmp_path, {"kind": "upload", "sha256": "d" * 64})
    state.object_store = _Unreachable()

    with caplog.at_level("WARNING"):
        assert _bound(state, "s1") == state.binding.config

    assert any("could not fetch" in r.getMessage() for r in caplog.records)


# --- what a member sees: the binding's origin, and a restore in the audit log (BE-0393 unit 7) ---


def _origin(state: srv.ServeState, session: str | None, org: str = "default") -> str:
    return state.binding_for(session, org).origin


def test_the_launch_binding_says_it_is_the_deployments(tmp_path: Path) -> None:
    # The three origins exist so a member can tell what they chose from what they were handed; the
    # configuration `serve` started with is the one nobody in a session chose.
    assert _origin(_state(tmp_path), None) == "deployment"


def test_a_bind_reads_as_the_members_own(tmp_path: Path) -> None:
    state = _state(tmp_path)
    (tmp_path / "mine.yaml").write_text(_CONFIG, encoding="utf-8")

    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200

    assert _origin(state, "s1") == "session"
    # A colleague reading the deployment's is still told so, not told it is theirs.
    assert _origin(state, "s2") == "deployment"


def test_a_sessionless_bind_stays_the_deployments(tmp_path: Path) -> None:
    """A shared-token or CI bind replaces the fallback, which is the deployment's binding however it
    got there — labelling it `session` would tell every other request it was theirs."""
    state = _state(tmp_path)
    (tmp_path / "mine.yaml").write_text(_CONFIG, encoding="utf-8")

    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200
    assert ops.bind_config(state, "mine.yaml", session=None)[1] == 200

    assert _origin(state, None) == "deployment"
    assert state.binding.origin == "deployment"
    # And a value that already carries a session's label cannot make the fallback claim to be one:
    # the slot decides the label, not what the caller hands over.
    state.rebind(None, "default", state.binding_for("s1", "default"))
    assert state.binding.origin == "deployment"


def test_a_restored_binding_says_it_was_inherited(tmp_path: Path) -> None:
    """The distinction the member most needs: this configuration is in front of them because their
    org last bound it, not because they opened it. The restore runs through the ordinary binder,
    which stamps `session` on the way in, so the relabelling has to survive that."""
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, _repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})

    assert _origin(state, "s1") == "inherited"
    # And it stays inherited on the reads that follow, which never re-enter the restore.
    assert _origin(state, "s1") == "inherited"


def test_binding_over_an_inherited_configuration_makes_it_the_members_own(tmp_path: Path) -> None:
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, _repo = _with_memory(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})
    (tmp_path / "mine.yaml").write_text(_CONFIG, encoding="utf-8")

    assert _origin(state, "s1") == "inherited"
    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200

    assert _origin(state, "s1") == "session"


def test_the_boot_read_names_the_origin(tmp_path: Path) -> None:
    # The header reads it from here; without it the UI would have to guess from the path alone.
    state = _state(tmp_path)
    (tmp_path / "mine.yaml").write_text(_CONFIG, encoding="utf-8")
    assert ops.bind_config(state, "mine.yaml", session="s1")[1] == 200

    payload, status = config_ops.config_info(state, actor="alice", session="s1")

    assert status == 200
    assert payload["configOrigin"] == "session"
    assert config_ops.config_info(state, actor="alice", session="s2")[0]["configOrigin"] == (
        "deployment"
    )


class _AuditingRepo(_MemoryRepo):
    """`_MemoryRepo` that also keeps the audit entries, which only unit 7 writes."""

    def __init__(self, source: object) -> None:
        super().__init__(source)
        self.audits: list[dict[str, object]] = []

    def record_audit(self, **entry: object) -> None:
        self.audits.append(entry)


def _auditing(tmp_path: Path, source: object) -> tuple[srv.ServeState, _AuditingRepo, str]:
    """A state whose org remembers *source*, with a real session belonging to `alice`."""
    state = _state(tmp_path)
    repo = _AuditingRepo(source)
    state.repository = repo  # type: ignore[assignment]
    state.restore_binding = partial(config_ops.restore_org_binding, state)
    return state, repo, state.auth.sessions.issue("alice")


def test_a_restore_is_audited_like_the_bind_it_stands_in_for(tmp_path: Path) -> None:
    """A restore puts a configuration in force without anyone asking for it, so the log that answers
    "what was this org running against, and since when" has to carry it too."""
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    source = {"kind": "file", "locator": {"path": str(remembered)}}
    state, repo, sid = _auditing(tmp_path, source)

    assert state.binding_for(sid, "default").config == remembered

    assert [e["action"] for e in repo.audits] == ["config.restore"]
    assert repo.audits[0]["actor_id"] == "alice"
    assert repo.audits[0]["org_id"] == "default"
    assert repo.audits[0]["detail"] == {"source": source}


def test_a_restored_bundle_is_audited_too(tmp_path: Path) -> None:
    # The bundle branch returns before the git/file tail, so it needs its own entry — and its own
    # test, since a call written once at the bottom would silently never run for an upload.
    uploads = tmp_path / "uploads"
    sha = "b" * 64
    (uploads / sha).mkdir(parents=True)
    (uploads / sha / "bajutsu.config.yaml").write_text(_CONFIG, encoding="utf-8")
    state, repo, sid = _auditing(tmp_path, {"kind": "upload", "sha256": sha})
    state.uploads_dir = uploads

    assert state.binding_for(sid, "default").upload is not None

    assert [e["action"] for e in repo.audits] == ["config.restore"]


def test_a_failed_restore_is_not_audited(tmp_path: Path) -> None:
    # Nothing came into force, so an entry would claim a change that never happened.
    state, repo, sid = _auditing(
        tmp_path, {"kind": "file", "locator": {"path": str(tmp_path / "gone.yaml")}}
    )

    assert state.binding_for(sid, "default").config == state.binding.config
    assert repo.audits == []


def test_an_unresolvable_bundle_is_not_audited(tmp_path: Path) -> None:
    state, repo, sid = _auditing(tmp_path, {"kind": "upload", "sha256": "c" * 64})

    assert state.binding_for(sid, "default").config == state.binding.config
    assert repo.audits == []


def test_a_restore_for_a_session_with_no_identity_records_nothing(tmp_path: Path) -> None:
    """A shared-token session has no actor to name, which is the same no-op every other action takes
    without one — the restore must still happen, and must not raise on the way."""
    remembered = tmp_path / "remembered.yaml"
    remembered.write_text(_CONFIG, encoding="utf-8")
    state, repo, _sid = _auditing(tmp_path, {"kind": "file", "locator": {"path": str(remembered)}})
    anonymous = state.auth.sessions.issue()

    assert state.binding_for(anonymous, "default").config == remembered
    assert repo.audits == []
