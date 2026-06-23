"""The config-declared org model (BE-0015 multi-tenancy): an `orgs:` block maps each org to its
member GitHub logins and its apps. A user or app not named in any org falls back to the single
`default` org, so a config with no `orgs:` block stays single-tenant."""

from __future__ import annotations

from bajutsu.config import (
    load_config,
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
    cfg = load_config(CONFIG_YAML)
    assert org_for_user(cfg, "alice") == "acme"
    assert org_for_user(cfg, "carol") == "globex"
    # A login in no org's members falls back to the default org.
    assert org_for_user(cfg, "stranger") == "default"


def test_org_for_target_resolves_ownership() -> None:
    cfg = load_config(CONFIG_YAML)
    assert org_for_target(cfg, "demo") == "acme"
    assert org_for_target(cfg, "other") == "globex"
    # An app in no org belongs to the default org.
    assert org_for_target(cfg, "unlisted") == "default"


def test_targets_for_org_lists_its_apps() -> None:
    cfg = load_config(CONFIG_YAML)
    assert sorted(targets_for_org(cfg, "acme")) == ["checkout", "demo"]
    assert targets_for_org(cfg, "globex") == ["other"]


def test_apps_for_default_org_are_the_unassigned_ones() -> None:
    cfg = load_config(CONFIG_YAML)
    # No app here is unassigned, so default owns none.
    assert targets_for_org(cfg, "default") == []


def test_targets_for_org_excludes_undeclared_app_names() -> None:
    # An org listing an app that has no `targets:` entry doesn't conjure a runnable app.
    cfg = load_config(
        "targets:\n  demo: { bundleId: com.example.demo }\n"
        "orgs:\n  acme:\n    members: [alice]\n    targets: [demo, ghost]\n"
    )
    assert targets_for_org(cfg, "acme") == ["demo"]


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
    cfg = load_config(IDENTITY_YAML)
    # alice is an explicit acme member, so her GitHub orgs don't override that.
    assert org_for_identity(cfg, "alice", ["globex-gh"]) == "acme"


def test_org_for_identity_maps_from_github_org_membership() -> None:
    cfg = load_config(IDENTITY_YAML)
    assert org_for_identity(cfg, "carol", ["globex-gh"]) == "globex"
    assert org_for_identity(cfg, "dave", ["acme-gh"]) == "acme"


def test_org_for_identity_falls_back_to_default() -> None:
    cfg = load_config(IDENTITY_YAML)
    # No explicit membership and no matching GitHub org → the default org.
    assert org_for_identity(cfg, "stranger", ["unrelated-gh"]) == "default"
    assert org_for_identity(cfg, "stranger", []) == "default"


def test_no_orgs_block_is_single_tenant() -> None:
    cfg = load_config("targets:\n  demo: { bundleId: com.example.demo }\n")
    assert org_for_user(cfg, "alice") == "default"
    assert org_for_target(cfg, "demo") == "default"
    # With no orgs declared, the default org owns every app.
    assert targets_for_org(cfg, "default") == ["demo"]
