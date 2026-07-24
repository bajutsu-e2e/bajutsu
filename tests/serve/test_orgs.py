"""The config-declared org model (BE-0015 multi-tenancy), owned by `serve` (BE-0129): an `orgs:`
block maps each org to its member GitHub logins and its targets. A user or target not named in any
org falls back to the single `default` org, so a config with no `orgs:` block stays single-tenant."""

from __future__ import annotations

import pytest

from bajutsu.serve.orgs import (
    identity_matches_org,
    load_serve_config,
    org_for_identity,
    org_for_target,
    org_for_user,
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


def test_org_for_user_resolves_membership() -> None:
    _, orgs = load_serve_config(CONFIG_YAML)
    assert org_for_user(orgs, "alice") == "acme"
    assert org_for_user(orgs, "carol") == "globex"
    # A login in no org's members falls back to the default org.
    assert org_for_user(orgs, "stranger") == "default"


def test_org_for_target_resolves_ownership() -> None:
    _, orgs = load_serve_config(CONFIG_YAML)
    assert org_for_target(orgs, "demo") == "acme"
    assert org_for_target(orgs, "other") == "globex"
    # A target in no org belongs to the default org.
    assert org_for_target(orgs, "unlisted") == "default"


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
    assert org_for_identity(orgs, "alice", ["globex-gh"]) == "acme"


def test_org_for_identity_maps_from_github_org_membership() -> None:
    _, orgs = load_serve_config(IDENTITY_YAML)
    assert org_for_identity(orgs, "carol", ["globex-gh"]) == "globex"
    assert org_for_identity(orgs, "dave", ["acme-gh"]) == "acme"


def test_org_for_identity_falls_back_to_default() -> None:
    _, orgs = load_serve_config(IDENTITY_YAML)
    # No explicit membership and no matching GitHub org → the default org.
    assert org_for_identity(orgs, "stranger", ["unrelated-gh"]) == "default"
    assert org_for_identity(orgs, "stranger", []) == "default"


def test_identity_matches_org_gates_sign_in() -> None:
    # BE-0313: the sign-in gate — an explicit member or a `githubOrgs` match belongs; anyone else
    # is turned away. Unlike org_for_identity, this can't confuse "matched nothing" with "default".
    _, orgs = load_serve_config(IDENTITY_YAML)
    assert identity_matches_org(orgs, "alice", []) is True  # explicit member
    assert identity_matches_org(orgs, "dave", ["acme-gh"]) is True  # githubOrgs match
    assert identity_matches_org(orgs, "stranger", ["unrelated-gh"]) is False
    assert identity_matches_org(orgs, "stranger", []) is False


def test_identity_matches_org_rejects_everyone_without_an_orgs_block() -> None:
    # No `orgs:` block → an empty mapping → nobody matches, so an OAuth deployment must declare one.
    _, orgs = load_serve_config("targets:\n  demo: { bundleId: com.x }\n")
    assert identity_matches_org(orgs, "alice", ["any-gh"]) is False


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
    assert org_for_user(orgs, "alice") == "default"
    assert org_for_target(orgs, "demo") == "default"
    # With no orgs declared, the default org owns every target.
    assert targets_for_org(orgs, cfg.targets, "default") == ["demo"]
