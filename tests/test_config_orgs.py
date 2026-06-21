"""The config-declared org model (BE-0015 multi-tenancy): an `orgs:` block maps each org to its
member GitHub logins and its apps. A user or app not named in any org falls back to the single
`default` org, so a config with no `orgs:` block stays single-tenant."""

from __future__ import annotations

from bajutsu.config import apps_for_org, load_config, org_for_app, org_for_user

CONFIG_YAML = """
apps:
  demo: { bundleId: com.example.demo }
  checkout: { bundleId: com.example.checkout }
  other: { bundleId: com.example.other }

orgs:
  acme:
    members: [alice, bob]
    apps: [demo, checkout]
  globex:
    members: [carol]
    apps: [other]
"""


def test_org_for_user_resolves_membership() -> None:
    cfg = load_config(CONFIG_YAML)
    assert org_for_user(cfg, "alice") == "acme"
    assert org_for_user(cfg, "carol") == "globex"
    # A login in no org's members falls back to the default org.
    assert org_for_user(cfg, "stranger") == "default"


def test_org_for_app_resolves_ownership() -> None:
    cfg = load_config(CONFIG_YAML)
    assert org_for_app(cfg, "demo") == "acme"
    assert org_for_app(cfg, "other") == "globex"
    # An app in no org belongs to the default org.
    assert org_for_app(cfg, "unlisted") == "default"


def test_apps_for_org_lists_its_apps() -> None:
    cfg = load_config(CONFIG_YAML)
    assert sorted(apps_for_org(cfg, "acme")) == ["checkout", "demo"]
    assert apps_for_org(cfg, "globex") == ["other"]


def test_apps_for_default_org_are_the_unassigned_ones() -> None:
    cfg = load_config(CONFIG_YAML)
    # No app here is unassigned, so default owns none.
    assert apps_for_org(cfg, "default") == []


def test_no_orgs_block_is_single_tenant() -> None:
    cfg = load_config("apps:\n  demo: { bundleId: com.example.demo }\n")
    assert org_for_user(cfg, "alice") == "default"
    assert org_for_app(cfg, "demo") == "default"
    # With no orgs declared, the default org owns every app.
    assert apps_for_org(cfg, "default") == ["demo"]
