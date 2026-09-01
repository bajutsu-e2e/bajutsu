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

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import Field, model_validator

from bajutsu.common import _yaml
from bajutsu.config import Config, _Model, parse_config_dict

if TYPE_CHECKING:  # keeps the default serve/CLI path free of `serve.server` (server/__init__.py)
    from bajutsu.serve.server.db import Repository


class OrgConfig(_Model):
    """One tenant under `orgs.<name>` (BE-0015 multi-tenancy).

    Holds the GitHub logins that belong to it (`members`), the GitHub orgs whose members belong to
    it (`github_orgs`), and/or the GitHub Teams whose direct members belong to it (`github_teams`),
    plus the targets it owns. A target named in no org falls back to the single `default` org.
    `editor_teams` (BE-0313) names the flat GitHub Teams, each as `"<github-org>/<team-slug>"`, whose
    direct members are promoted to editor within this org; an empty list leaves every member of the
    org at viewer.

    Each `github_teams` entry has that same `"<github-org>/<team-slug>"` shape and admits its direct
    members to this org at viewer, so a deployment can grant a single Team access without granting
    its whole GitHub organization — the narrower unit a GitHub organization's own structure already
    models. `editor_teams` admits as well as promotes: a Team whose members may write is a Team whose
    members may sign in, and requiring it to be repeated under `github_teams` would make "may write
    but cannot log in" a configuration an operator can write by accident.

    A list rather than the single Team it started as (BE-0375 unit 9), for the reason
    `BAJUTSU_OAUTH_ADMIN_TEAMS` is one: `github_orgs` and `github_teams` are lists because one org
    may span several GitHub organizations, and a single `editor_teams` slot cannot then name a
    writing Team per organization — the only ways out being to merge Teams on GitHub's side or to
    keep one roster by hand, which is the manual maintenance BE-0313 removed.
    """

    members: list[str] = Field(default_factory=list)
    github_orgs: list[str] = Field(default_factory=list, alias="githubOrgs")
    github_teams: list[str] = Field(default_factory=list, alias="githubTeams")
    editor_teams: list[str] = Field(default_factory=list, alias="editorTeams")
    targets: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fold_retired_editor_team(cls, data: Any) -> Any:
        """Fold the retired singular `editorTeam` key into `editor_teams` (BE-0375 unit 9).

        Accepted rather than rejected, and folded in rather than preferred one way or the other: this
        model is `extra="forbid"`, so an un-renamed key would raise out of `parse_orgs`, and
        `load_serve_config_file` answers a parse failure with *no* org model — every login of a
        deployment that missed one key would be turned away under "user not allowed", the silent
        lockout BE-0352's retired-name warning exists to prevent. `BAJUTSU_OAUTH_ADMIN_TEAM` could
        take that route because an unread environment variable still leaves a config that loads.

        A deployment that sets both keys (the likelier partial rename — an operator adds the plural
        name and leaves the singular one behind) keeps both Teams, so neither spelling silently
        loses the role it was written to grant.
        """
        if not isinstance(data, dict) or "editorTeam" not in data:
            return data
        data = dict(data)
        retired = data.pop("editorTeam")
        existing = data.get("editorTeams")
        # `editorTeam: ""` and a missing key both meant "no editor Team" before the rename, so an
        # empty value folds to nothing rather than to an entry that matches no Team but does make
        # `orgs_declaring_membership` count this org as having a roster.
        if retired is None or (isinstance(retired, str) and not retired.strip()):
            return data
        if existing is None or isinstance(existing, list):
            data["editorTeams"] = [*(existing or []), retired]
        return data

    def admitting_teams(self) -> list[str]:
        """Every Team whose direct members this org admits — `github_teams` plus `editor_teams`.

        One accessor, so the sign-in gate and every "does this org declare a membership" check read
        the same union and cannot disagree about whether `editor_teams` alone admits anyone.
        """
        return [*self.github_teams, *self.editor_teams]


# The single tenant every unassigned user and target falls into.
DEFAULT_ORG = "default"


def in_teams(teams: Sequence[str], wanted: Iterable[str]) -> bool:
    """Whether any of *teams* is one of *wanted*, the single Team-membership test behind the sign-in
    gate, the editor role, and the server-wide admin Team — so a Team that admits a login and the
    role that login resolves to can never drift apart.

    Lowercased on both sides, since GitHub resolves an org login and a Team slug
    case-insensitively and `identity.teams` reports GitHub's own casing either way. `str.lower`
    rather than `str.casefold`: full case folding equates names GitHub keeps distinct (`gruß` and
    `gruss` can be two Teams of one organization), and since BE-0375 unit 8 a match buys sign-in, so
    anyone able to create the folded-equal Team would clear the gate. Lowercasing equates nothing
    beyond ASCII case, which is what GitHub's own slug lowercasing implies; it never turns an empty
    Team name into a match, and preserves the nested-Team guarantee, which rests on exact string
    equality of the full `"<github-org>/<team-slug>"` (BE-0352).
    """
    lowered = {t.lower() for t in wanted}
    return any(team.lower() in lowered for team in teams)


def _match_orgs(
    orgs: dict[str, OrgConfig], login: str, github_orgs: list[str], teams: Sequence[str]
) -> list[str]:
    """Every org *login* belongs to, best match first — empty when no declared org admits it.

    The one place the three membership axes are ranked, so the sign-in gate and the placement below
    can never admit a login into one org while resolving it to another. An explicit `members` entry
    wins, then an intersection with some org's `github_orgs`, then direct membership in one of an
    org's admitting Teams (`githubTeams` or its `editorTeams`). Teams rank last so that adding one to
    an org never relocates a login an existing `members`/`githubOrgs` entry already placed.

    Order within an axis is deterministic but source-dependent; see `org_for_identity`. An org
    matching on two axes appears once, at its best rank.

    Empty rather than `[DEFAULT_ORG]` for "matched nothing", so a login no entry lists stays
    distinguishable from one an org literally named `default` lists as a member.
    """
    user_orgs = set(github_orgs)
    matched: list[str] = [org for org, oc in orgs.items() if login in oc.members]
    matched += [
        org
        for org, oc in orgs.items()
        if org not in matched and user_orgs.intersection(oc.github_orgs)
    ]
    matched += [
        org
        for org, oc in orgs.items()
        if org not in matched and in_teams(teams, oc.admitting_teams())
    ]
    return matched


def identity_matches_org(
    orgs: dict[str, OrgConfig], login: str, github_orgs: list[str], teams: Sequence[str]
) -> bool:
    """Whether *login* (with GitHub memberships *github_orgs* and *teams*) belongs to any declared
    org (BE-0313).

    True when the login is an explicit `members` entry, a member of some org's `github_orgs`, or a
    direct member of one of some org's admitting Teams. The sign-in gate consults this before
    `org_for_identity`, whose plain `str` return can't tell a login that matched nothing from one
    that legitimately resolved to `default` — and a deployment may name an org literally `default`.
    An empty `orgs` mapping — no `orgs:` block, or a config that failed to load — matches nobody, so
    this gate alone admits no login; `oauth_callback` admits a configured admin Team's members
    alongside it, so a deployment can still recover from a missing or broken block.

    *teams* has no default, and neither does `org_for_identity`'s: a caller that could omit the
    Team axis is exactly how the gate and the placement would come to consult different axes again,
    and it would type-check clean while silently denying or misplacing a Team-admitted login.
    """
    return bool(_match_orgs(orgs, login, github_orgs, teams))


def orgs_for_identity(
    orgs: dict[str, OrgConfig], login: str, github_orgs: list[str], teams: Sequence[str]
) -> list[str]:
    """Every org a user logging in as *login* may act as, best match first.

    The full ranked list behind `org_for_identity`'s single answer, so a login belonging to several
    orgs can be offered the choice between them instead of being pinned to the head. Empty for a
    login no declared org admits — including one admitted by the admin-Team bypass, whose eligible
    set is decided by that bypass rather than by any org's membership.
    """
    return _match_orgs(orgs, login, github_orgs, teams)


def preferred_org(eligible: Sequence[str]) -> str:
    """The org a login lands in when it has picked none: the best match, else `DEFAULT_ORG`.

    The one place the "head of the ranking, else the fallback" rule lives, so the sign-in placement
    and `org_for_identity` cannot come to disagree about what an empty eligible set means.
    """
    return eligible[0] if eligible else DEFAULT_ORG


def org_for_identity(
    orgs: dict[str, OrgConfig], login: str, github_orgs: list[str], teams: Sequence[str]
) -> str:
    """The org for a user logging in as *login* with the given GitHub *github_orgs* and *teams*
    memberships (BE-0015).

    An explicit `members` listing wins; otherwise the first org whose `github_orgs` intersects the
    user's GitHub orgs; otherwise the first org one of whose admitting Teams the user is a direct
    member of; otherwise `default`.

    "First" is deterministic but source-dependent, which matters only when two orgs name the same
    GitHub organization or Team: `parse_orgs` preserves the order the `orgs:` block declares them in,
    while `orgs_from_db` iterates slug order (`list_orgs` sorts by it). Both are stable — the same
    login resolves the same way on every sign-in — but a deployment holding such an overlap can see
    the tie-break move once, at the conversion to the database (BE-0375). The tie-break decides only
    where such a login *starts*: `orgs_for_identity` hands back the whole ranked list, and a user who
    picks another org from it keeps that pick across sign-ins.

    Sign-in composes the same two steps itself — `orgs_for_identity` for the list it stores, then
    `preferred_org` for the placement — because it needs the list either way. This is the one-call
    form of that answer, for a caller holding an identity and wanting only the org.
    """
    return preferred_org(_match_orgs(orgs, login, github_orgs, teams))


def targets_for_org(
    orgs: dict[str, OrgConfig],
    targets: Iterable[str],
    org: str,
    *,
    bound_by: str | None = None,
) -> list[str]:
    """The targets belonging to *org*, restricted to *targets* actually declared under `targets:`.

    An org that lists an undeclared target name doesn't conjure a runnable target. For `default`,
    that's every declared target no org claims.

    *bound_by* names the org that bound this configuration through the API — an uploaded bundle, a
    composed triple, or a Git source (BE-0375). The `orgs:` block is then not consulted at all: the
    bundle was uploaded *as* that org, so every target it declares is that org's and no other org's.
    Reading ownership out of a file the deployment does not control is the same trust problem this
    item already refused for membership, and it fails silently — an entry claiming a target for an
    org the reader is not in leaves them a target list that is simply empty, with nothing said. The
    launch configuration passes None and keeps the `orgs:`-declared ownership, which is the
    multi-tenant deployment shape an operator writes by hand.
    """
    declared = list(targets)
    if bound_by is not None:
        return declared if org == bound_by else []
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
        raise ValueError("orgs: must be a mapping of org name to its config")  # noqa: TRY004  # invalid external payload, not a caller type error
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
            githubTeams=list(row.github_teams),
            editorTeams=list(row.editor_teams),
        )
        for row in repository.list_orgs()
    }


def orgs_declaring_membership(orgs: dict[str, OrgConfig]) -> list[str]:
    """The entries that declare `members` / `githubOrgs` / `githubTeams` / `editorTeams` (BE-0375).

    An entry carrying only `targets` is the end state a database-backed deployment is meant to
    reach, since target ownership stays in configuration, so it is never one of these — which is
    what lets the caller warn about the rest without firing forever on a correct configuration.
    """
    return [
        name for name, oc in orgs.items() if oc.members or oc.github_orgs or oc.admitting_teams()
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
            github_teams=list(oc.github_teams),
            editor_teams=list(oc.editor_teams),
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
