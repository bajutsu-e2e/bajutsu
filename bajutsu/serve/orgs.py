"""The config-declared org model for `serve` (BE-0015 multi-tenancy, BE-0129).

Hosting is a `serve` concern the deterministic core does not model, so the `orgs:` block and its
resolution helpers live here rather than in `bajutsu/config`. `load_serve_config` parses a raw
config once, splitting it into the core `Config` (org-agnostic) and the org model the serve auth /
storage layer resolves against.

That model has two producers (BE-0375): `parse_orgs` from the `orgs:` block, and `orgs_from_db`
from the database a hosted deployment runs against. They yield the same `{name: OrgConfig}` shape,
so every resolution helper below is unchanged by which one a deployment reads — only `targets`
differs, since target ownership stays in configuration either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import Field

from bajutsu import _yaml
from bajutsu.config import Config, _Model, parse_config_dict

if TYPE_CHECKING:  # keeps the default serve/CLI path free of `serve.server` (server/__init__.py)
    from bajutsu.serve.server.db import Repository


class OrgConfig(_Model):
    """One tenant under `orgs.<name>` (BE-0015 multi-tenancy).

    Holds the GitHub logins that belong to it (`members`) and/or the GitHub orgs whose members
    belong to it (`github_orgs`), plus the targets it owns. A target named in no org falls back to
    the single `default` org. `editor_team` (BE-0313) names one flat GitHub Team, as
    `"<github-org>/<team-slug>"`, whose direct members are promoted to editor within this org; None
    leaves every member of the org at viewer.
    """

    members: list[str] = Field(default_factory=list)
    github_orgs: list[str] = Field(default_factory=list, alias="githubOrgs")
    editor_team: str | None = Field(default=None, alias="editorTeam")
    targets: list[str] = Field(default_factory=list)


# The single tenant every unassigned user and target falls into.
DEFAULT_ORG = "default"


def org_for_user(orgs: dict[str, OrgConfig], login: str) -> str:
    """The org whose members list *login*, or `default` if none do."""
    return next((org for org, oc in orgs.items() if login in oc.members), DEFAULT_ORG)


def identity_matches_org(orgs: dict[str, OrgConfig], login: str, github_orgs: list[str]) -> bool:
    """Whether *login* (with GitHub memberships *github_orgs*) belongs to any declared org (BE-0313).

    True when the login is an explicit `members` entry or a member of some org's `github_orgs`. The
    sign-in gate consults this before `org_for_identity`, whose plain `str` return can't tell a login
    that matched nothing from one that legitimately resolved to `default` — and a deployment may name
    an org literally `default`. An empty `orgs` mapping — no `orgs:` block, or a config that failed
    to load — matches nobody, so this gate alone admits no login; `oauth_callback` admits a
    configured admin Team's members alongside it, so a deployment can still recover from a missing
    or broken block.
    """
    if any(login in oc.members for oc in orgs.values()):
        return True
    user_orgs = set(github_orgs)
    return any(user_orgs.intersection(oc.github_orgs) for oc in orgs.values())


def org_for_identity(orgs: dict[str, OrgConfig], login: str, github_orgs: list[str]) -> str:
    """The org for a user logging in as *login* with the given GitHub *github_orgs* memberships (BE-0015).

    An explicit `members` listing wins; otherwise the first org whose `github_orgs` intersects the
    user's GitHub orgs; otherwise `default`.

    "First" is deterministic but source-dependent, which matters only when two orgs name the same
    GitHub organization: `parse_orgs` preserves the order the `orgs:` block declares them in, while
    `orgs_from_db` iterates slug order (`list_orgs` sorts by it). Both are stable — the same login
    resolves the same way on every sign-in — but a deployment holding such an overlap can see the
    tie-break move once, at the conversion to the database (BE-0375). A login that belongs to more
    than one org has no way to say which it means today; letting them choose is a separate item.
    """
    explicit = org_for_user(orgs, login)
    if explicit != DEFAULT_ORG:
        return explicit
    user_orgs = set(github_orgs)
    return next(
        (org for org, oc in orgs.items() if user_orgs.intersection(oc.github_orgs)),
        DEFAULT_ORG,
    )


def targets_for_org(orgs: dict[str, OrgConfig], targets: Iterable[str], org: str) -> list[str]:
    """The targets belonging to *org*, restricted to *targets* actually declared under `targets:`.

    An org that lists an undeclared target name doesn't conjure a runnable target. For `default`,
    that's every declared target no org claims.
    """
    declared = list(targets)
    if org == DEFAULT_ORG:
        claimed = {a for oc in orgs.values() for a in oc.targets}
        return [a for a in declared if a not in claimed]
    oc = orgs.get(org)
    return [a for a in oc.targets if a in declared] if oc else []


def parse_orgs(orgs_block: object) -> dict[str, OrgConfig]:
    """Validate a raw `orgs:` mapping into `{name: OrgConfig}`.

    A missing/`null` block (or an empty mapping) yields `{}`. Any other present-but-non-mapping
    value (a string, number, or list) is a config error, not silently ignored — so a malformed
    `orgs:` fails loudly rather than collapsing to single-tenant.
    """
    if orgs_block is None:
        return {}
    if not isinstance(orgs_block, dict):
        raise ValueError("orgs: must be a mapping of org name to its config")
    return {name: OrgConfig.model_validate(body or {}) for name, body in orgs_block.items()}


def orgs_from_db(repository: Repository) -> dict[str, OrgConfig]:
    """The org model read from the database — `parse_orgs`'s shape from a second source (BE-0375).

    Assembles the identical `{name: OrgConfig}` mapping the `orgs:` block produces, keyed by each
    row's id (the same string `state.org_of` hands back as `org_id`), so every membership consumer
    resolves against it unchanged. `targets` is always empty: an org's target ownership stays in
    configuration, resolved through `targets_for_org` against the config-parsed model.

    Unlike `load_serve_config_file`, a read failure propagates rather than collapsing to an empty
    mapping. An empty mapping means "no org matched"; a database `serve` cannot read must answer
    with an error naming the database, not deny every user by blaming their GitHub membership.
    """
    return {
        # Built through the field aliases, the same names the `orgs:` block itself uses, so this
        # producer and `parse_orgs` construct the identical model from the identical key names.
        row.id: OrgConfig(
            members=list(row.members),
            githubOrgs=list(row.github_orgs),
            editorTeam=row.editor_team,
        )
        for row in repository.list_orgs()
    }


def orgs_declaring_membership(orgs: dict[str, OrgConfig]) -> list[str]:
    """The entries that declare `members` / `githubOrgs` / `editorTeam` (BE-0375).

    An entry carrying only `targets` is the end state a database-backed deployment is meant to
    reach, since target ownership stays in configuration, so it is never one of these — which is
    what lets the caller warn about the rest without firing forever on a correct configuration.
    """
    return [
        name
        for name, oc in orgs.items()
        if oc.members or oc.github_orgs or oc.editor_team is not None
    ]


def seed_orgs_from_config(repository: Repository, orgs: dict[str, OrgConfig]) -> list[str]:
    """Seed each config-declared org's membership into the database (BE-0375).

    Run once, at startup, against an `orgs` table that holds no row at all — the caller owns that
    condition. One boot converts a configuration-only deployment; afterwards the database is the
    sole author of its own roster. `seed_org_membership` still skips a row already marked seeded or
    soft-deleted, so a partially converted table (a passive row an earlier sign-in left behind)
    cannot be seeded twice.

    An entry declaring only `targets` is skipped rather than seeded, so the cutover marker is never
    spent on a roster nobody wrote. That entry is legitimate in both directions: it is the end state
    the docs recommend *after* the conversion, and paring the config down before the converting boot
    must not be the irreversible act of locking every org at "admits nobody". Its row is therefore
    left uncreated — target ownership resolves from the configuration, not the table.

    Returns the names of the entries that declared membership and were nonetheless *not* seeded, so
    the caller can tell an operator that configuration no longer decides them.
    """
    stale: list[str] = []
    for name in orgs_declaring_membership(orgs):
        oc = orgs[name]
        seeded = repository.seed_org_membership(
            name,
            slug=name,
            name=name,
            members=list(oc.members),
            github_orgs=list(oc.github_orgs),
            editor_team=oc.editor_team,
        )
        if not seeded:
            stale.append(name)
    return stale


def load_serve_config(text: str) -> tuple[Config, dict[str, OrgConfig]]:
    """Parse a raw config into the core `Config` plus its org model (BE-0129).

    The document is parsed once: `serve` validates the `orgs:` block locally, while
    `parse_config_dict` builds the org-agnostic `Config` (dropping `orgs:` itself).
    """
    data = _yaml.safe_load(text) or {}
    orgs = parse_orgs(data.get("orgs") if isinstance(data, dict) else None)
    return parse_config_dict(data), orgs
