"""Structural tests for the Orgs admin page (BE-0375 unit 5).

Modelled on the Projects page's own UI tests: the page markup ships from the index, its JS ships as
the serve.orgs.mjs ES module, and that module targets the four `/api/orgs…` endpoints. The behaviour
is covered by the operation tests in test_org_lifecycle.py; here we pin that the surface exists,
addresses the right endpoints, and keeps the two invariants a reader of the module cannot see from
the endpoints alone — the tab ships hidden (only a list answer from an admin on a database-backed
deployment reveals it), and only one membership form is ever open, since its input ids are
singletons and a second form would make a save write one org's roster onto another.
"""

from __future__ import annotations

import re
from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _fetch_many(tmp_path: Path, *routes: str) -> list[str]:
    """Every *route*'s body from one server, so a test comparing two assets builds the tree once."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return [_get(port, route)[1].decode("utf-8") for route in routes]
    finally:
        server.shutdown()
        server.server_close()


def _fetch(tmp_path: Path, route: str) -> str:
    return _fetch_many(tmp_path, route)[0]


def test_orgs_tab_and_view_ship_with_the_tab_hidden(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-view="orgs"' in text
    assert 'data-testid="nav.orgs-view" hidden' in text  # revealed only by a list answer
    assert 'data-testid="view.orgs"' in text
    assert 'data-testid="orgs.host"' in text  # the client-rendered list host


def test_orgs_create_form_ships(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-testid="orgs.add-slug"' in text
    assert 'data-testid="orgs.add-name"' in text
    assert 'data-testid="orgs.add-submit"' in text
    assert 'data-testid="orgs.error"' in text


def test_js_wires_all_four_org_endpoints(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.orgs.mjs")
    assert "'/api/orgs'" in text  # GET the list and POST a create
    assert "/membership" in text  # POST the whole-value membership replacement
    assert "DELETE" in text  # per-row retire
    assert "renderOrgsView" in text


def test_js_renders_a_display_name_that_differs_from_the_slug(tmp_path: Path) -> None:
    # The create form collects a display name and no endpoint can change it afterwards, so a value
    # no row ever showed would leave a typo permanent and invisible. Rendered only when it differs
    # from the slug the row already leads with, so the common case is not printed twice.
    text = _fetch(tmp_path, "/serve.orgs.mjs")
    body = text.split("function orgRow")[1].split("\n}")[0]
    assert "o.name !== o.slug" in body and "esc(o.name)" in body


def test_js_offers_no_control_on_the_reserved_fallback_row(tmp_path: Path) -> None:
    # The server refuses all three mutations on `default`, so the row must not offer buttons that
    # can only answer 409 — and it must still be rendered, since an admin admitted by the bypass is
    # the one sitting in it.
    text = _fetch(tmp_path, "/serve.orgs.mjs")
    body = text.split("function orgRow")[1].split("\n}")[0]
    assert "o.reserved" in body
    assert 'data-act="edit"' in body and "disabled" in body


def test_js_keeps_one_membership_form_open_at_a_time(tmp_path: Path) -> None:
    # The form's input ids (`#orgs-members` and friends) are singletons, so two open forms would
    # make every save read the first form's fields — silently replacing one org's roster with
    # another's. `openMembership` re-renders before inserting, and binds through the row it just
    # inserted after rather than through document order.
    text = _fetch(tmp_path, "/serve.orgs.mjs")
    body = text.split("function openMembership")[1].split("\n}")[0]
    # Ordering, not source text: the bare name also matches the cancel handler's own
    # `renderOrgsView()` at the end of the body, so `in body` alone would survive deleting the
    # re-render this test exists to pin.
    assert body.index("renderOrgsView") < body.index("insertAdjacentHTML")
    assert "nextElementSibling" in body and ".orgedit" in body  # bound via the row just inserted


def test_header_carries_an_org_badge_fed_by_the_config_read(tmp_path: Path) -> None:
    # The org a session acts as is shown beside the config it acts on, from the same boot read, and
    # stays hidden until that read names an identity — a local serve must look exactly as it did.
    index, core = _fetch_many(tmp_path, "/", "/serve.core.mjs")
    assert 'data-testid="nav.org"' in index
    assert 'id="orgbadge"' in index and "hidden>" in index.split('id="orgbadge"')[1][:80]
    body = core.split("function setOrgBadge")[1].split("\n}")[0]
    assert "el.hidden=!show" in body  # hidden unless both fields came back
    assert "setOrgBadge(c.actor,c.org,c.orgs)" in core  # fed by the /api/config boot read


def test_header_switches_org_only_when_there_is_more_than_one(tmp_path: Path) -> None:
    # The switcher ships hidden and stays hidden for a single-org session: with nothing to choose,
    # the header keeps the read-only badge it has always had. Both controls are driven from the one
    # boot read, so exactly one of them is ever visible.
    index, core = _fetch_many(tmp_path, "/", "/serve.core.mjs")
    assert 'data-testid="nav.org-switch"' in index
    assert 'id="orgsw"' in index and "hidden>" in index.split('id="orgsw"')[1][:120]
    body = core.split("function setOrgBadge")[1].split("\n}")[0]
    assert "choices.length>1" in body  # one org offers no choice
    assert "el.hidden=!show||pick" in body  # the badge gives way to the switcher, never both
    assert "sw.hidden=!pick" in body
    assert "$('#orgsw').addEventListener" in core  # the select is actually wired


def test_switching_org_reloads_the_page(tmp_path: Path) -> None:
    # Every tab is silently scoped to the active org, so a partial refresh would leave
    # already-rendered views showing the previous tenant's runs and projects.
    core = _fetch(tmp_path, "/serve.core.mjs")
    body = core.split("async function switchOrg")[1].split("\n}")[0]
    assert "postJSON('/api/org'" in body
    assert "location.reload()" in body
    # A refusal re-syncs the select instead of leaving it lying — by re-rendering the header alone,
    # not by re-running `loadConfig`, whose tail pops the "Open config" modal with no config bound.
    assert "setOrgBadge(c.actor,c.org,c.orgs)" in body
    assert "loadConfig()" not in body


def test_show_view_toggles_every_declared_view(tmp_path: Path) -> None:
    # The bug this exists for: the Orgs tab, its section, and its refresh hook all shipped, every
    # test passed, and the page was unreachable — `showView` sets `.hidden` on one `#view-<name>` per
    # view by hand, and `#view-orgs` was missing from that line, so selecting the tab hid every other
    # view and revealed nothing. Asserted over every tab the shell declares rather than over `orgs`
    # alone, since the hand-written line is one edit away from dropping the next view the same way.
    index, core = _fetch_many(tmp_path, "/", "/serve.core.mjs")
    views = set(re.findall(r'data-view="([a-z]+)"', index))
    assert "orgs" in views  # the regex has to be finding tabs at all for the rest to mean anything
    missing = [v for v in sorted(views) if f"#view-{v}" not in core]
    assert not missing, f"showView never unhides these declared views: {missing}"


def test_the_orgs_view_gets_the_single_column_layout(tmp_path: Path) -> None:
    # The second thing only a browser showed: the page rendered, in a ~340px column with every row
    # truncated. A `main` view is a grid, and the single-card views name themselves in one rule to
    # take the full width. Any single-card view added later needs that rule too — shipping the
    # markup without it is exactly the miss this pins.
    css = _fetch(tmp_path, "/")  # serve.css is inlined into the index
    rule = next(
        line
        for line in css.splitlines()
        if "grid-template-columns:1fr}" in line and "#view-" in line
    )
    assert "#view-orgs" in rule and "#view-metrics" in rule


def test_core_js_refreshes_the_orgs_page_on_entry(tmp_path: Path) -> None:
    # Another admin may have created or retired an org since this tab was last opened.
    text = _fetch(tmp_path, "/serve.core.mjs")
    assert "loadOrgs()" in text


def test_js_asks_for_the_roster_only_where_the_server_offers_one(tmp_path: Path) -> None:
    # The tab's visibility used to be decided purely by a non-list answer, which meant a
    # database-less serve was asked on every load and answered 400 in the console. The boot read's
    # capability block now decides whether to ask at all; the shape check stays as the fallback for
    # a role that changed since boot (#1721).
    text = _fetch(tmp_path, "/serve.orgs.mjs")
    body = text.split("async function loadOrgs")[1].split("\n}")[0]
    assert "unavailableReason('orgs')" in body
    assert "blocked ?" in body and "await getJSON('/api/orgs'" in body  # the fetch sits behind it
    assert "Array.isArray(list)" in body


def test_js_keeps_the_capability_block_when_a_later_config_read_fails(tmp_path: Path) -> None:
    # loadConfig runs again on a rebind or a project switch, and getJSON resolves to its fallback on
    # any transient failure. Resetting the block there would read as "unknown", which the helper
    # treats as available — re-enabling Capture on a deployment that 404s it (#1721).
    text = _fetch(tmp_path, "/serve.core.mjs")
    assert "if(c.capabilities&&typeof c.capabilities==='object')capabilities=c.capabilities" in text
