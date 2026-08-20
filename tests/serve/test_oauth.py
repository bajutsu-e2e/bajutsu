"""Tests for the GitHub OAuth login operations (BE-0015 7b-2, BE-0313).

`oauth_login` / `oauth_callback` are provider-neutral: they drive the injected `OAuthClient` seam,
verify the CSRF state, and gate sign-in on GitHub org membership (BE-0313) before minting a session,
deriving the role from GitHub Team membership. A `FakeOAuthClient` stands in for GitHub so the gate
never makes a network call."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bajutsu.serve import operations as ops
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.state import ServeState, SessionManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

# An `orgs:` block that admits `alice` (explicit member) and anyone in the `acme-gh` GitHub org, and
# names the Team whose members become editors. The sign-in gate reads this from `state.config`.
_ORGS_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    members: [alice]
    githubOrgs: [acme-gh]
    editorTeams: [acme-gh/scenario-maintainers]
    targets: [demo]
"""


class FakeOAuthClient:
    """The slice of the OAuth flow the operations use, in memory — no GitHub call. `fetch_identity`
    returns None for the code ``"bad"`` (a failed exchange), else the configured login + orgs +
    teams."""

    def __init__(
        self,
        login: str | None = "alice",
        orgs: list[str] | None = None,
        teams: list[str] | None = None,
    ) -> None:
        self._login = login
        self._orgs = orgs or []
        self._teams = teams or []

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        if code == "bad" or not self._login:
            return None
        return Identity(login=self._login, orgs=list(self._orgs), teams=list(self._teams))


def _config_file(tmp_path: Path, body: str = _ORGS_YAML) -> Path:
    path = tmp_path / "serve.config.yaml"
    path.write_text(body)
    return path


def _state(
    tmp_path: Path,
    *,
    oauth: object = None,
    config: Path | None = None,
    admin_teams: list[str] | None = None,
) -> ServeState:
    return ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=oauth, oauth_admin_teams=tuple(admin_teams or ())),
    )


def test_oauth_login_not_configured(tmp_path: Path) -> None:
    _payload, status, csrf = ops.oauth_login(_state(tmp_path))
    assert status == 404
    assert csrf is None


def test_oauth_login_returns_redirect_carrying_the_state(tmp_path: Path) -> None:
    payload, status, csrf = ops.oauth_login(_state(tmp_path, oauth=FakeOAuthClient()))
    assert status == 200
    assert csrf and csrf in payload["redirect"]  # the CSRF state rides in the authorize URL


def test_oauth_callback_rejects_when_oauth_is_not_configured(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A half-configured deployment (one of the three BAJUTSU_OAUTH_GITHUB_* vars unset) 404s here
    # for every GitHub sign-in -- not a lockout, since `login`'s shared-token path re-enables itself
    # on this same `oauth is None`, but still needs a record. `oauth is None` is a static property
    # of the deployment an anonymous caller can hit at request rate, so this records at INFO, not a
    # per-request WARNING -- the loud once-per-boot signal lives in `server.startup_warning`.
    state = _state(tmp_path, config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 404
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


def test_oauth_callback_rejects_a_state_mismatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # No login is known this early -- a repeated mismatch is the signature of a login-CSRF attempt,
    # not just an expired cookie, so it still needs its own record. But `state_param`/`state_cookie`
    # are both caller-supplied (a query value and the caller's own Cookie: header), so nothing here
    # distinguishes an attack from an expired cookie on any single request -- this records at INFO,
    # not a per-request WARNING an anonymous caller can trigger at request rate (BE-0352).
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="x", state_cookie="y"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


def test_oauth_callback_rejects_an_anonymous_probe_with_no_state_at_all(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A callback carrying no state at all (no cookie, no query value) is the cheapest possible
    # unauthenticated request against this endpoint -- also INFO, for the same reason as a real
    # mismatch above.
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="", state_cookie=""
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


def test_oauth_callback_bypasses_csrf_with_matching_fake_state_then_records_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An attacker who fully controls both the query value and their own Cookie: header can satisfy
    # `secrets.compare_digest(state_param, state_cookie)` for free by sending the same fake value as
    # both -- this clears the CSRF check with no real GitHub auth, then fails the exchange (a
    # garbage `code`) and records under the same INFO-level path as the branches above (BE-0352).
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="bad", state_param="fake", state_cookie="fake"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


def test_oauth_callback_allows_an_org_member_and_binds_identity(tmp_path: Path) -> None:
    # alice is an explicit `members` entry, so the org gate admits her (BE-0313).
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None
    assert state.auth.valid_session(sid)
    assert state.auth.sessions.identity(sid) == "alice"  # the session is bound to the GitHub login


def test_oauth_callback_rejects_a_user_in_no_org(tmp_path: Path) -> None:
    # mallory is neither an explicit member nor in a `githubOrgs` org, so the gate turns them away.
    state = _state(tmp_path, oauth=FakeOAuthClient(login="mallory"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


def test_oauth_callback_rejects_when_no_orgs_block_is_configured(tmp_path: Path) -> None:
    # BE-0313: with no `orgs:` block, the org roster is empty, so every login is rejected — an OAuth
    # deployment must declare one.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path, body)
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


def test_oauth_callback_rejects_a_failed_exchange(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Reachable with no real GitHub auth (see the CSRF-bypass test above), so this records at INFO.
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="bad", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


class _RaisingOAuthClient:
    """An OAuth client whose exchange raises (e.g. a network error or missing authlib)."""

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        raise RuntimeError("github unreachable")


def _db_state(
    serve_engine: Callable[..., Engine],
    tmp_path: Path,
    oauth: object,
    admin_teams: list[str] | None = None,
    config: Path | None = None,
) -> tuple[ServeState, Engine]:
    from bajutsu.serve.operations.config import seed_orgs_from_bound_config
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    state = _state(
        tmp_path, oauth=oauth, config=config or _config_file(tmp_path), admin_teams=admin_teams
    )
    state.repository = SqlRepository(engine)
    # A database-backed deployment resolves sign-in against the `orgs` table, not the `orgs:` block
    # (BE-0375), and `serve()` seeds that table from the bound config at startup and at every
    # rebind. Do the same here, or every test below would be exercising an empty roster.
    seed_orgs_from_bound_config(state)
    return state, engine


def _role_after_login(state: ServeState, login: str) -> str | None:
    _payload, status, _sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert state.repository is not None
    return state.repository.user_role(login)


def test_oauth_callback_persists_the_user_under_the_resolved_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # With a database wired, a successful login upserts the user into their resolved org so
    # audit/RBAC can reference them (BE-0015 7c-1).
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import Org, User

    state, engine = _db_state(serve_engine, tmp_path, FakeOAuthClient(login="alice"))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        users = list(s.scalars(select(User)))
        orgs = list(s.scalars(select(Org)))
    assert [u.github_login for u in users] == ["alice"]
    assert [o.slug for o in orgs] == ["acme"]


def test_oauth_callback_base_role_is_viewer(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # BE-0313: a signed-in user in no editor/admin Team gets the base viewer role.
    state, _ = _db_state(serve_engine, tmp_path, FakeOAuthClient(login="alice", teams=[]))
    assert _role_after_login(state, "alice") == "viewer"


def test_oauth_callback_editor_team_membership_promotes_to_editor(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="alice", teams=["acme-gh/scenario-maintainers"]),
    )
    assert _role_after_login(state, "alice") == "editor"


# An `orgs:` block whose only roster is a Team: `qa` admits a Team directly, `writers` admits
# through its `editorTeams` alone. Neither declares a member or a GitHub org, so a login reaching
# either one got there on Team membership and nothing else.
_TEAM_ORGS_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  qa:
    githubTeams: [acme-gh/qa]
    targets: [demo]
  writers:
    editorTeams: [acme-gh/scenario-maintainers]
"""


def test_oauth_callback_admits_a_team_only_login_into_its_team_s_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A `githubTeams` entry is a sign-in axis of its own: erin is in nobody's `members` and no
    # `githubOrgs` list, and GitHub reports no org for her at all, yet her Team places her in `qa`.
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="erin", orgs=[], teams=["acme-gh/qa"]),
        config=_config_file(tmp_path, _TEAM_ORGS_YAML),
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is not None
    # Placed in the org that admitted her, not the `default` fallback -- being admitted by one org
    # and filed under another would give her another tenant's targets and object-storage prefix.
    assert state.repository.user_org("erin") == "qa"
    assert state.repository.user_role("erin") == "viewer"


def test_oauth_callback_admits_a_login_through_the_editor_team_alone(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # `writers` declares nothing but `editorTeams`, so that Team is its whole roster: frank signs
    # in on it and lands in `writers` as an editor -- "may write but cannot log in" is not a state
    # this configuration can express.
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="frank", orgs=[], teams=["acme-gh/scenario-maintainers"]),
        config=_config_file(tmp_path, _TEAM_ORGS_YAML),
    )
    assert _role_after_login(state, "frank") == "editor"
    assert state.repository is not None
    assert state.repository.user_org("frank") == "writers"


def test_oauth_callback_denies_a_team_only_login_whose_teams_github_withheld(
    serve_engine: Callable[..., Engine], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # `_fetch_teams` fails closed -- it never invents a Team -- so a login whose only roster is a
    # Team is denied while `/user/teams` is down. Fail-closed is the right direction for a gate, but
    # the denial must name what GitHub withheld, or an operator edits a roster that was never wrong.
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="erin", orgs=[], teams=[]),
        config=_config_file(tmp_path, _TEAM_ORGS_YAML),
    )
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert "GitHub returned no orgs or teams for this login" in record.getMessage()


def test_oauth_callback_admin_team_membership_promotes_to_admin(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="alice", teams=["acme-gh/ops"]),
        admin_teams=["acme-gh/ops"],
    )
    assert _role_after_login(state, "alice") == "admin"


def test_oauth_callback_admin_team_bypasses_the_org_gate_with_no_matching_org(
    tmp_path: Path,
) -> None:
    # mallory belongs to no configured org, but is a member of the admin Team: the admin-Team
    # bypass admits her regardless, so she can sign in and repoint a broken `orgs:` config.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None


def test_oauth_callback_admin_team_bypass_warning_names_a_missing_orgs_block(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # This is the item's own headline scenario (Motivation): no `orgs:` block declared at all.
    # mallory has real GitHub orgs, so an operator must not be told "GitHub returned no orgs" --
    # that would send them chasing GitHub when the fix is declaring an `orgs:` block.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", orgs=["acme-gh"], teams=["ops-gh/root"]),
        config=_config_file(tmp_path, body),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.WARNING):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert "declares no orgs: block" in record.getMessage()
    assert "GitHub returned no orgs" not in record.getMessage()


def test_oauth_callback_denial_names_a_missing_orgs_block(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The complement of the test above, for the outright-rejected login rather than the bypass.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", orgs=["acme-gh"], teams=["some-other/team"]),
        config=_config_file(tmp_path, body),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert "declares no orgs: block" in record.getMessage()
    assert "no org membership matched this login" not in record.getMessage()
    # admin_teams is configured (non-empty) but mallory isn't in it -- a real membership miss, not
    # an unconfigured admin_teams, so the message must say "matched," not "configured," and this
    # denial is INFO, not the WARNING reserved for the no-admin-Team-at-all case.
    assert "no admin Team matched" in record.getMessage()
    assert "no admin Team is configured" not in record.getMessage()
    assert record.levelno == logging.INFO


def test_oauth_callback_denial_names_a_github_orgs_outage_not_the_org_roster(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # bob is a real githubOrgs member of `acme`, but /user/orgs is down (identity.orgs == []) and
    # he carries no admin Team, so he is denied -- not by a bad roster (`acme` already claims him),
    # but by the outage. The message must blame GitHub, or an operator edits an `orgs:` block that
    # was never wrong.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="bob", orgs=[], teams=["some-other/team"]),
        config=_config_file(tmp_path),
    )
    with caplog.at_level(logging.WARNING):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert "GitHub returned no orgs for this login" in record.getMessage()
    assert "no org membership matched this login" not in record.getMessage()
    # No admin_teams is configured at all here (the default) -- distinct from a real membership
    # miss, and the state admin_teams_empty warns about at boot, so it must say "configured," not
    # "matched," or an operator reads a Team-membership problem where the fix is setting
    # BAJUTSU_OAUTH_ADMIN_TEAMS.
    assert "no usable admin Team is configured" in record.getMessage()
    assert "no admin Team matched" not in record.getMessage()
    # No admin can sign in to fix anything on this deployment -- the shape worth paging on.
    assert record.levelno == logging.WARNING


def test_oauth_callback_denial_warns_when_admin_teams_is_entirely_malformed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A space-separated value ("acme-gh/ops other-gh/root") parses to ONE entry that can never
    # match a real Team -- `admin_teams` is truthy, but functionally identical to an empty tuple.
    # `not admin_teams` alone would call this an ordinary INFO denial; it must warn like the
    # genuinely-empty case above, since nobody can sign in to fix orgs: here either.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="bob", orgs=[], teams=["some-other/team"]),
        config=_config_file(tmp_path),
        admin_teams=["acme-gh/ops other-gh/root"],
    )
    with caplog.at_level(logging.WARNING):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.WARNING
    # The message must match the level: "no admin Team matched" reads as a real membership miss,
    # which would send an operator to check GitHub Team membership instead of the malformed entry.
    assert "no usable admin Team is configured" in record.getMessage()
    assert "no admin Team matched" not in record.getMessage()


def test_oauth_callback_denial_stays_info_when_only_some_admin_teams_entries_are_malformed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A partly-malformed list is NOT the lockout an empty or entirely-malformed one is: an admin in
    # `acme-gh/ops` can still sign in and repair `orgs:`, so this must read as an ordinary membership
    # miss at INFO. This mixed shape is the only input that tells `admin_teams_unusable`'s `all(...)`
    # apart from `any(...)`; without it, that quantifier is unpinned.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="bob", orgs=[], teams=["some-other/team"]),
        config=_config_file(tmp_path),
        admin_teams=["acme-gh/ops", "other-gh/root extra"],
    )
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO
    assert "no admin Team matched" in record.getMessage()
    assert "no usable admin Team is configured" not in record.getMessage()


def test_oauth_callback_ordinary_admin_team_bypass_logs_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The bypass is the one sign-in path `orgs:` did not authorize, so it's the one an operator
    # auditing sign-ins would otherwise have no record of at all -- but a config that loaded clean
    # and simply doesn't list this admin's org (the deliberate ops-only-org shape this item's own
    # docs recommend) is the normal, permanent operating state of a correctly configured deployment,
    # not something worth paging on. INFO still leaves a record.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert "admin-Team bypass admitted mallory" in caplog.text
    # Pin the structured fields too: a bare `_logger.info(...)` carrying the same message text
    # would pass the assertion above but leave `event`/`actor`/`bypass` off the record, breaking
    # exactly the alert an operator would key on `event=oauth.login` for.
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert record.levelno == logging.INFO
    assert record.actor == "mallory"
    assert record.bypass is True


def test_oauth_callback_admin_team_bypass_logs_a_warning_when_no_config_is_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The complement of the test above: with no config bound at all (parsed is None), the bypass
    # admitted a login into a deployment nobody but an admin Team member can currently sign in to
    # repair -- the recovery state this item exists for, and the one worth paging on.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )  # config left at its None default
    with caplog.at_level(logging.WARNING):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert record.levelno == logging.WARNING
    assert record.bypass is True


def test_oauth_callback_org_member_does_not_log_a_bypass_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # alice is an explicit org member *and* an admin-Team member: the ordinary org gate admits her,
    # so the bypass never fires -- oauth.login must still record the login, but with bypass=False,
    # not the "admin-Team bypass" message that only a bypassing admission gets.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="alice", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert "admin-Team bypass" not in caplog.text
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert record.levelno == logging.INFO  # WARNING is reserved for a bypass admission
    assert record.bypass is False
    assert record.actor == "alice"


def test_oauth_callback_admin_team_bypasses_the_org_gate_with_no_orgs_block(
    tmp_path: Path,
) -> None:
    # No `orgs:` block at all — every login would normally be rejected (BE-0313) — but the admin
    # Team bypass still admits an admin so they can fix the config.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path, body),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None


def test_oauth_callback_admin_team_bypass_resolves_to_admin_role(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # An `orgs:` block IS declared (`_ORGS_YAML`'s `acme`) and mallory matches none of it — the
    # case BE-0313 called unreachable through OAuth sign-in.
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    assert _role_after_login(state, "mallory") == "admin"


def test_oauth_callback_admin_team_bypass_resolves_to_admin_role_with_no_orgs_block(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The other case the Progress checklist claims: no `orgs:` block at all, with a database wired
    # (so `role_for` actually runs) — the resolved role must still be admin.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
        config=_config_file(tmp_path, body),
    )
    assert _role_after_login(state, "mallory") == "admin"


def test_oauth_callback_admin_team_bypass_places_the_user_in_the_default_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import Org

    # An `orgs:` block IS declared (`_ORGS_YAML`'s `acme`) and mallory matches none of it — the
    # case BE-0313 called unreachable through OAuth sign-in, so `default` is created by the bypass.
    state, engine = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        orgs = list(s.scalars(select(Org)))
    # `acme` is there because the startup seed put `_ORGS_YAML`'s entry in the table (BE-0375);
    # `default` is the row this bypass sign-in created, which is what this test is about.
    assert sorted(o.slug for o in orgs) == ["acme", "default"]


def test_oauth_callback_admin_team_bypass_relocates_on_a_transient_orgs_fetch_failure(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # bob reaches `acme` only through `githubOrgs` (not an explicit `members` entry). A later
    # /user/orgs failure makes him look unmatched even though `acme` still claims him -- `authz.py`
    # can't tell that apart from a login that genuinely belongs to no GitHub org at all (a
    # `members:`-listed bot/ops account, say), and guarding on it would pin such a login to its old
    # org forever once revoked, since it could never again report a non-empty orgs list. This is the
    # accepted, self-healing cost of that ambiguity: bob is relocated to `default` for this one
    # login, same as a genuinely un-claimed login, and moves back to `acme` on his next clean login.
    # The org model itself being unreadable is no longer one of these ambiguous cases: the database
    # decides org placement now, and a failure to read it never reaches this path at all (see
    # test_oauth_callback_signs_in_from_the_database_when_the_config_cannot_load).
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=[])
    )
    assert _role_after_login(state, "bob") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("bob") == "acme"

    state.auth.oauth = FakeOAuthClient(login="bob", orgs=[], teams=["ops-gh/root"])
    state.auth.oauth_admin_teams = ("ops-gh/root",)
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "default"

    # His next clean login (orgs answered again) moves him back.
    state.auth.oauth = FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=["ops-gh/root"])
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "acme"


def test_oauth_callback_admin_team_bypass_reresolves_a_revoked_members_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The complement of the test above: GitHub *did* answer /user/orgs, so a login matching no
    # `orgs:` entry is genuinely un-claimed -- an operator who removed them must see that take
    # effect on the next sign-in (BE-0015 7c-2), not stay pinned to the org they used to hold.
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=[])
    )
    assert _role_after_login(state, "bob") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("bob") == "acme"

    # bob has since been removed from `acme-gh`, but still reports some other org: not a fetch
    # failure, so the bypass admits him and he re-resolves to `default`.
    state.auth.oauth = FakeOAuthClient(login="bob", orgs=["ops-gh"], teams=["ops-gh/root"])
    state.auth.oauth_admin_teams = ("ops-gh/root",)
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "default"


def test_oauth_callback_signs_in_from_the_database_when_the_config_cannot_load(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # BE-0375's headline: with a database wired, a config that fails to load no longer denies
    # anyone. It used to collapse `orgs` to `{}` and turn every non-admin sign-in into a 403 -- on a
    # deployment whose database already knew exactly who carol was -- which BE-0313 could only
    # soften with an org-recovery guard, and only for a user already on record. carol is in no admin
    # Team and has never signed in here, so nothing but the database can admit her.
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="carol", orgs=["acme-gh"], teams=[])
    )
    state.config = tmp_path / "missing.yaml"  # never written -- load_serve_config_file -> None
    assert _role_after_login(state, "carol") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("carol") == "acme"


def test_oauth_callback_signs_in_from_the_database_when_no_config_is_bound(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The same, for the other shape `load_serve_config_file` answers None for: no config bound at
    # all. Once the database is the source, unbinding the config changes nothing about who may sign
    # in as which org, so the two shapes stop needing to be told apart on this path at all.
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="carol", orgs=["acme-gh"], teams=[])
    )
    state.config = None
    assert _role_after_login(state, "carol") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("carol") == "acme"


def test_oauth_callback_bypass_names_no_config_bound_not_a_load_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The complement of the message-triage tests above, for the fifth shape: an operator must not
    # be told the config "failed to load" when none was ever bound -- that sends them hunting a
    # filesystem error or a YAML typo in a file that doesn't exist yet.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )  # config left at its None default
    with caplog.at_level(logging.WARNING):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert "no serve config is bound" in record.getMessage()
    assert "failed to load" not in record.getMessage()


def test_oauth_callback_denial_names_no_config_bound_not_a_load_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["some-other/team"]),
        admin_teams=["ops-gh/root"],
    )  # config left at its None default
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert "no serve config is bound" in record.getMessage()
    assert "failed to load" not in record.getMessage()
    assert record.levelno == logging.INFO  # admin_teams is configured; WARNING is not this shape


def test_oauth_callback_rejects_a_login_in_neither_the_org_gate_nor_the_admin_teams(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # mallory reports a real (but non-matching) GitHub org, so this hits the final branch --
    # neither a config-load failure, nor a missing `orgs:` block, nor an empty GitHub orgs list.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", orgs=["some-other-gh"], teams=["some-other/team"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    # The rejection is the one failure this item exists to make recoverable, so it must leave a
    # record too -- under its own event, not `oauth.login`, which stays "login count" (BE-0015 7c-1
    # audit-style visibility, not just a raw 403 with nothing an operator can correlate on).
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.actor == "mallory"
    # Worded for either source (BE-0375): the same final branch is reached from an `orgs:` block
    # and from the `orgs` table, and naming one of them would mislead half the deployments.
    assert "no org membership matched this login" in record.getMessage()
    assert record.levelno == logging.INFO  # admin_teams is configured; WARNING is not this shape


def test_oauth_callback_denial_names_a_config_load_failure_not_the_org_roster(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # When the config itself fails to load, `orgs` collapses to `{}` and every non-admin login is
    # denied -- the message must blame the config, not an org roster that was never actually read,
    # or an operator chasing it edits `orgs:` while the real fault is the unreadable file.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["some-other/team"]),
        config=tmp_path / "missing.yaml",  # never written -- load_serve_config_file -> None
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert "the serve config failed to load" in record.getMessage()
    assert "no org membership matched this login" not in record.getMessage()
    assert record.levelno == logging.INFO  # admin_teams is configured; WARNING is not this shape


def test_oauth_callback_without_a_database_is_a_no_op(tmp_path: Path) -> None:
    # No repository (the default): login still works, nothing is persisted.
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is None


def test_oauth_callback_surfaces_an_exchange_error_as_502(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A raising exchange (network / token parsing / missing dep) is an upstream error, not a 500.
    # Reachable with no real GitHub auth (see the CSRF-bypass test above), so this records at INFO.
    state = _state(tmp_path, oauth=_RaisingOAuthClient(), config=_config_file(tmp_path))
    with caplog.at_level(logging.INFO):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 502
    assert sid is None
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.levelno == logging.INFO


class _FakeResponse:
    def __init__(self, status: int, body: object, next_url: str | None = None) -> None:
        self.status_code = status
        self._body = body
        self.links = {"next": {"url": next_url}} if next_url else {}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _PagingClient:
    """A stand-in httpx client whose collection is paginated across pages."""

    def __init__(self, pages: list[_FakeResponse]) -> None:
        self._pages = pages
        self.calls = 0

    def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        resp = self._pages[self.calls]
        self.calls += 1
        return resp


def test_fetch_orgs_follows_pagination() -> None:
    from bajutsu.serve.server.oauth import _fetch_orgs

    client = _PagingClient(
        [
            _FakeResponse(200, [{"login": "acme-gh"}], next_url="https://api.github.test/p2"),
            _FakeResponse(200, [{"login": "globex-gh"}]),
        ]
    )
    assert _fetch_orgs(client, {}) == ["acme-gh", "globex-gh"]
    assert client.calls == 2  # both pages fetched


def test_fetch_orgs_is_non_fatal_on_error() -> None:
    from bajutsu.serve.server.oauth import _fetch_orgs

    assert _fetch_orgs(_PagingClient([_FakeResponse(403, [])]), {}) == []
    assert _fetch_orgs(_PagingClient([_FakeResponse(200, ValueError("bad json"))]), {}) == []
    # A 200 whose body isn't a list (e.g. a GitHub error payload shaped as an object) is non-fatal too.
    assert (
        _fetch_orgs(_PagingClient([_FakeResponse(200, {"message": "bad credentials"})]), {}) == []
    )


def _team(org: str, slug: str) -> dict[str, object]:
    return {"slug": slug, "organization": {"login": org}}


def test_fetch_teams_parses_org_slug_and_follows_pagination() -> None:
    from bajutsu.serve.server.oauth import _fetch_teams

    client = _PagingClient(
        [
            _FakeResponse(
                200,
                [_team("acme-gh", "scenario-maintainers")],
                next_url="https://api.github.test/p2",
            ),
            _FakeResponse(200, [_team("acme-gh", "ops")]),
        ]
    )
    # A Team on a later page still resolves (BE-0313), each as "<org>/<slug>".
    assert _fetch_teams(client, {}) == ["acme-gh/scenario-maintainers", "acme-gh/ops"]
    assert client.calls == 2


def test_fetch_teams_fails_closed_to_empty_on_error() -> None:
    # BE-0313: the opposite failure direction from _fetch_orgs — an empty list leaves the user at
    # viewer, so a failed lookup never grants write access.
    from bajutsu.serve.server.oauth import _fetch_teams

    assert _fetch_teams(_PagingClient([_FakeResponse(403, [])]), {}) == []
    assert _fetch_teams(_PagingClient([_FakeResponse(200, ValueError("bad json"))]), {}) == []
    # A malformed item (missing organization/slug) is skipped, not fatal.
    assert _fetch_teams(_PagingClient([_FakeResponse(200, [{"slug": "x"}])]), {}) == []
    # A 200 whose body isn't a list never grants a role — the fail-closed direction matters most here.
    assert (
        _fetch_teams(_PagingClient([_FakeResponse(200, {"message": "bad credentials"})]), {}) == []
    )
