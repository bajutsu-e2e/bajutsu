"""The config-declared org model (BE-0015 multi-tenancy), owned by `serve` (BE-0129): an `orgs:`
block maps each org to its member GitHub logins and its targets. A user or target not named in any
org falls back to the single `default` org, so a config with no `orgs:` block stays single-tenant."""

from __future__ import annotations

import pytest

from bajutsu.serve.orgs import (
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


def test_malformed_orgs_block_fails_loudly() -> None:
    # An `orgs:` that isn't a mapping (org name -> config) is a config error, not silently ignored.
    with pytest.raises(ValueError, match="orgs: must be a mapping"):
        load_serve_config("targets:\n  demo: { bundleId: com.x }\norgs:\n  - not-a-mapping\n")


def test_no_orgs_block_is_single_tenant() -> None:
    cfg, orgs = load_serve_config("targets:\n  demo: { bundleId: com.example.demo }\n")
    assert orgs == {}
    assert org_for_user(orgs, "alice") == "default"
    assert org_for_target(orgs, "demo") == "default"
    # With no orgs declared, the default org owns every target.
    assert targets_for_org(orgs, cfg.targets, "default") == ["demo"]
