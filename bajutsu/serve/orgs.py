"""The config-declared org model for `serve` (BE-0015 multi-tenancy, BE-0129).

Hosting is a `serve` concern the deterministic core does not model, so the `orgs:` block and its
resolution helpers live here rather than in `bajutsu/config.py`. `load_serve_config` parses a raw
config once, splitting it into the core `Config` (org-agnostic) and the org model the serve auth /
storage layer resolves against.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from bajutsu import _yaml
from bajutsu.config import Config, _Model, parse_config_dict


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


def org_for_target(orgs: dict[str, OrgConfig], target: str) -> str:
    """The org whose targets list *target*, or `default` if none do."""
    return next((org for org, oc in orgs.items() if target in oc.targets), DEFAULT_ORG)


def identity_matches_org(orgs: dict[str, OrgConfig], login: str, github_orgs: list[str]) -> bool:
    """Whether *login* (with GitHub memberships *github_orgs*) belongs to any declared org (BE-0313).

    True when the login is an explicit `members` entry or a member of some org's `github_orgs`. The
    sign-in gate consults this before `org_for_identity`, whose plain `str` return can't tell a login
    that matched nothing from one that legitimately resolved to `default` — and a deployment may name
    an org literally `default`. An empty `orgs` mapping (no `orgs:` block) matches nobody, so an
    OAuth deployment must declare one to admit any login.
    """
    if any(login in oc.members for oc in orgs.values()):
        return True
    user_orgs = set(github_orgs)
    return any(user_orgs.intersection(oc.github_orgs) for oc in orgs.values())


def org_for_identity(orgs: dict[str, OrgConfig], login: str, github_orgs: list[str]) -> str:
    """The org for a user logging in as *login* with the given GitHub *github_orgs* memberships (BE-0015).

    An explicit `members` listing wins; otherwise the first org whose `github_orgs` intersects the
    user's GitHub orgs; otherwise `default`. Resolution is deterministic in config order.
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


def load_serve_config(text: str) -> tuple[Config, dict[str, OrgConfig]]:
    """Parse a raw config into the core `Config` plus its org model (BE-0129).

    The document is parsed once: `serve` validates the `orgs:` block locally, while
    `parse_config_dict` builds the org-agnostic `Config` (dropping `orgs:` itself).
    """
    data = _yaml.safe_load(text) or {}
    orgs = parse_orgs(data.get("orgs") if isinstance(data, dict) else None)
    return parse_config_dict(data), orgs
