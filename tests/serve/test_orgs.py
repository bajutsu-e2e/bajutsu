"""The config-declared org model (BE-0015 multi-tenancy), owned by `serve` (BE-0129): an `orgs:`
block maps each org to its member GitHub logins and its targets. A user or target not named in any
org falls back to the single `default` org, so a config with no `orgs:` block stays single-tenant."""

from __future__ import annotations

import pytest

from bajutsu.serve.orgs import (
    identity_matches_org,
    load_serve_config,
    org_for_identity,
    targets_for_org,
)

CONFIG_YAML = """
targets:
  demo: { bundleId: com.example.demo }
  checkout: { bundleId: com.example.checkout }
  other: { bundleId: com.example.other }

orgs:
  acme:
    members: [alice, bob]
    targets: [demo, checkout]
  globex:
    members: [carol]
    targets: [other]
"""


def test_an_explicit_member_resolves_to_its_org() -> None:
    _, orgs = load_serve_config(CONFIG_YAML)
    assert org_for_identity(orgs, "alice", [], []) == "acme"
    assert org_for_identity(orgs, "carol", [], []) == "globex"
    # A login in no org's members falls back to the default org.
    assert org_for_identity(orgs, "stranger", [], []) == "default"


def test_targets_for_org_lists_its_targets() -> None:
    cfg, orgs = load_serve_config(CONFIG_YAML)
    assert sorted(targets_for_org(orgs, cfg.targets, "acme")) == ["checkout", "demo"]
    assert targets_for_org(orgs, cfg.targets, "globex") == ["other"]


def test_targets_for_default_org_are_the_unassigned_ones() -> None:
    cfg, orgs = load_serve_config(CONFIG_YAML)
    # No target here is unassigned, so default owns none.
    assert targets_for_org(orgs, cfg.targets, "default") == []


def test_targets_for_org_excludes_undeclared_target_names() -> None:
    # An org listing a target that has no `targets:` entry doesn't conjure a runnable target.
    cfg, orgs = load_serve_config(
        "targets:\n  demo: { bundleId: com.example.demo }\n"
        "orgs:\n  acme:\n    members: [alice]\n    targets: [demo, ghost]\n"
    )
    assert targets_for_org(orgs, cfg.targets, "acme") == ["demo"]


IDENTITY_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    members: [alice]
    githubOrgs: [acme-gh]
    targets: [demo]
  globex:
    githubOrgs: [globex-gh]
"""


def test_org_for_identity_prefers_an_explicit_member() -> None:
    _, orgs = load_serve_config(IDENTITY_YAML)
    # alice is an explicit acme member, so her GitHub orgs don't override that.
    assert org_for_identity(orgs, "alice", ["globex-gh"], []) == "acme"


def test_org_for_identity_maps_from_github_org_membership() -> None:
    _, orgs = load_serve_config(IDENTITY_YAML)
    assert org_for_identity(orgs, "carol", ["globex-gh"], []) == "globex"
    assert org_for_identity(orgs, "dave", ["acme-gh"], []) == "acme"


def test_org_for_identity_falls_back_to_default() -> None:
    _, orgs = load_serve_config(IDENTITY_YAML)
    # No explicit membership and no matching GitHub org → the default org.
    assert org_for_identity(orgs, "stranger", ["unrelated-gh"], []) == "default"
    assert org_for_identity(orgs, "stranger", [], []) == "default"


def test_identity_matches_org_gates_sign_in() -> None:
    # BE-0313: the sign-in gate — an explicit member or a `githubOrgs` match belongs; anyone else
    # is turned away. Unlike org_for_identity, this can't confuse "matched nothing" with "default".
    _, orgs = load_serve_config(IDENTITY_YAML)
    assert identity_matches_org(orgs, "alice", [], []) is True  # explicit member
    assert identity_matches_org(orgs, "dave", ["acme-gh"], []) is True  # githubOrgs match
    assert identity_matches_org(orgs, "stranger", ["unrelated-gh"], []) is False
    assert identity_matches_org(orgs, "stranger", [], []) is False


def test_identity_matches_org_rejects_everyone_without_an_orgs_block() -> None:
    # No `orgs:` block → an empty mapping → nobody matches, so an OAuth deployment must declare one.
    _, orgs = load_serve_config("targets:\n  demo: { bundleId: com.x }\n")
    assert identity_matches_org(orgs, "alice", ["any-gh"], []) is False


def test_identity_matches_org_handles_an_org_literally_named_default() -> None:
    # identity_matches_org exists precisely because org_for_identity(...) != DEFAULT_ORG would
    # wrongly reject a deployment that names an org "default" — its members legitimately resolve to
    # that sentinel string, which the naive check would confuse with "matched nothing."
    _, orgs = load_serve_config(
        "targets:\n  demo: { bundleId: com.x }\norgs:\n  default:\n    members: [alice]\n"
    )
    assert identity_matches_org(orgs, "alice", [], []) is True
    assert identity_matches_org(orgs, "stranger", [], []) is False


def test_editor_team_parses_from_editor_team_alias() -> None:
    # BE-0313: `editorTeam` on an org names the flat Team whose members are editors.
    _, orgs = load_serve_config(
        "targets:\n  demo: { bundleId: com.x }\n"
        "orgs:\n  acme:\n    githubOrgs: [acme-gh]\n    editorTeam: acme-gh/scenario-maintainers\n"
    )
    assert orgs["acme"].editor_team == "acme-gh/scenario-maintainers"
    # Absent by default.
    _, plain = load_serve_config(
        "targets:\n  demo: { bundleId: com.x }\norgs:\n  acme:\n    githubOrgs: [acme-gh]\n"
    )
    assert plain["acme"].editor_team is None


TEAM_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    githubOrgs: [acme-gh]
    targets: [demo]
  globex:
    githubTeams: [globex-gh/qa, globex-gh/ops]
  initech:
    editorTeam: initech-gh/scenario-maintainers
"""


def test_github_teams_admit_and_place_a_login() -> None:
    # A Team membership is a sign-in axis of its own: nobody in globex's `githubOrgs` (it declares
    # none) and no explicit member, yet a direct member of one of its Teams belongs to it.
    _, orgs = load_serve_config(TEAM_YAML)
    assert identity_matches_org(orgs, "erin", [], ["globex-gh/ops"]) is True
    # Admitted *into globex*, not the default fallback — the gate and the placement read one ranking,
    # so a login admitted through a Team is never filed somewhere its Team does not appear.
    assert org_for_identity(orgs, "erin", [], ["globex-gh/ops"]) == "globex"
    # An unlisted Team is no membership at all.
    assert identity_matches_org(orgs, "erin", [], ["globex-gh/interns"]) is False
    assert org_for_identity(orgs, "erin", [], ["globex-gh/interns"]) == "default"


def test_editor_team_also_admits_a_login() -> None:
    # initech declares no members and no githubOrgs, so its editorTeam is its whole roster: a Team
    # whose members may write is a Team whose members may sign in.
    _, orgs = load_serve_config(TEAM_YAML)
    teams = ["initech-gh/scenario-maintainers"]
    assert identity_matches_org(orgs, "frank", [], teams) is True
    assert org_for_identity(orgs, "frank", [], teams) == "initech"


def test_team_matching_is_case_insensitive() -> None:
    # GitHub resolves an org login and a Team slug case-insensitively, and `identity.teams` reports
    # GitHub's own casing — so a config written in another case must not lock its Team out.
    _, orgs = load_serve_config(TEAM_YAML)
    assert identity_matches_org(orgs, "erin", [], ["Globex-GH/QA"]) is True
    assert org_for_identity(orgs, "erin", [], ["Globex-GH/QA"]) == "globex"


def test_team_matching_stops_at_ascii_case() -> None:
    # `str.lower`, not `str.casefold`: full folding equates `gruß` with `gruss`, which GitHub keeps
    # distinct, so a Team anyone in that organization can create would clear the sign-in gate.
    _, orgs = load_serve_config(
        "targets:\n  demo: { bundleId: com.x }\n"
        "orgs:\n  acme:\n    githubTeams: [acme-gh/gru\u00df]\n"
    )
    assert identity_matches_org(orgs, "mallory", [], ["acme-gh/gruss"]) is False
    assert identity_matches_org(orgs, "erin", [], ["ACME-GH/GRU\u00df"]) is True


def test_a_nested_team_does_not_match_its_parent() -> None:
    # `/user/teams` lists a child Team distinct from its parent, and matching is exact on the whole
    # `"<github-org>/<team-slug>"`, so a Team nested under a listed one is not admitted by it.
    _, orgs = load_serve_config(TEAM_YAML)
    assert identity_matches_org(orgs, "erin", [], ["globex-gh/qa/nested"]) is False


def test_teams_never_relocate_a_login_an_org_axis_already_placed() -> None:
    # Teams rank last, so declaring one cannot move a login some `members`/`githubOrgs` entry
    # already claimed — adding an axis must not silently re-file existing users.
    _, orgs = load_serve_config(TEAM_YAML)
    assert org_for_identity(orgs, "dave", ["acme-gh"], ["globex-gh/qa"]) == "acme"


def test_github_teams_parse_from_the_github_teams_alias() -> None:
    _, orgs = load_serve_config(TEAM_YAML)
    assert orgs["globex"].github_teams == ["globex-gh/qa", "globex-gh/ops"]
    # Absent by default, and `admitting_teams` unions the two Team fields.
    assert orgs["acme"].github_teams == []
    assert orgs["acme"].admitting_teams() == []
    assert orgs["initech"].admitting_teams() == ["initech-gh/scenario-maintainers"]


def test_malformed_orgs_block_fails_loudly() -> None:
    # An `orgs:` that isn't a mapping (org name -> config) is a config error, not silently ignored.
    with pytest.raises(ValueError, match="orgs: must be a mapping"):
        load_serve_config("targets:\n  demo: { bundleId: com.x }\norgs:\n  - not-a-mapping\n")


def test_present_but_falsy_orgs_block_fails_loudly() -> None:
    # A present-but-non-mapping scalar (e.g. `orgs: ""`) must not silently collapse to single-tenant;
    # only a missing/null block or an empty mapping is treated as "no orgs".
    with pytest.raises(ValueError, match="orgs: must be a mapping"):
        load_serve_config('targets:\n  demo: { bundleId: com.x }\norgs: ""\n')


def test_empty_orgs_mapping_is_single_tenant() -> None:
    # An empty mapping (`orgs: {}`) is a legitimate "no orgs", not an error.
    cfg, orgs = load_serve_config("targets:\n  demo: { bundleId: com.x }\norgs: {}\n")
    assert orgs == {}
    assert targets_for_org(orgs, cfg.targets, "default") == ["demo"]


def test_no_orgs_block_is_single_tenant() -> None:
    cfg, orgs = load_serve_config("targets:\n  demo: { bundleId: com.example.demo }\n")
    assert orgs == {}
    assert org_for_identity(orgs, "alice", [], []) == "default"
    # With no orgs declared, the default org owns every target.
    assert targets_for_org(orgs, cfg.targets, "default") == ["demo"]
