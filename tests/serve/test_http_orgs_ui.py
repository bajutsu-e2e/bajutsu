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

from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _fetch(tmp_path: Path, route: str) -> str:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return _get(port, route)[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


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


def test_core_js_refreshes_the_orgs_page_on_entry(tmp_path: Path) -> None:
    # Another admin may have created or retired an org since this tab was last opened.
    text = _fetch(tmp_path, "/serve.core.mjs")
    assert "loadOrgs()" in text
