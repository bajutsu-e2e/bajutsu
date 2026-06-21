"""Tests for scripts/allocate_roadmap_ids.py.

The script allocates permanent BE IDs to ``BE-XXXX`` placeholders and, in ``--repair`` mode,
renumbers an item whose id a more authoritative holder already owns — ``origin/main`` (the BE-0054
double-allocation) or, when nothing is merged, a lower-numbered open PR. These tests pin the pure
pieces (reserved- and lower-PR-id parsing, the used-id floor, collision detection) over temporary
trees, the git-mv side effects over throwaway git repos, and finally assert the committed roadmaps/
tree carries no duplicate id.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "allocate_roadmap_ids.py"
_spec = importlib.util.spec_from_file_location("allocate_roadmap_ids", _MODULE_PATH)
assert _spec and _spec.loader
ari = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ari
_spec.loader.exec_module(ari)


def _make_item(roadmap: Path, category: str, name: str, body: str = "") -> Path:
    """Create a minimal BE item (both language files) that self-references its id token."""
    be_id = "-".join(name.split("-")[:2])  # "BE-0054-foo" -> "BE-0054" (or "BE-XXXX")
    item = roadmap / category / name
    item.mkdir(parents=True)
    (item / f"{name}.md").write_text(
        f"# {be_id} — demo\n\n* Proposal: [{be_id}]({name}.md)\n{body}", encoding="utf-8"
    )
    (item / f"{name}-ja.md").write_text(f"# {be_id} — demo\n", encoding="utf-8")
    return item


def _git_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tmp_path a throwaway git repo with everything staged, isolated from the outer repo.

    A git hook (pre-push running `make check`) exports GIT_DIR / GIT_INDEX_FILE into this process;
    left set they redirect the nested git calls at the outer repo. Clear them, then init + add. A
    throwaway identity (and no commit signing) is set locally so a test can commit even on a fresh
    CI runner with no global git config — without it `git commit` exits 128 ("who are you?").
    """
    for var in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    config = (
        ("user.email", "test@example.com"),
        ("user.name", "test"),
        ("commit.gpgsign", "false"),
    )
    for key, value in config:
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)


# --- reserved ids / used-id floor ------------------------------------------------


def test_reserved_ids_from_env_parses_any_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "54 51")
    assert ari.reserved_ids_from_env() == {54, 51}
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "BE-0054, BE-0007")  # tokens, comma-separated
    assert ari.reserved_ids_from_env() == {54, 7}


def test_reserved_ids_from_env_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "")
    assert ari.reserved_ids_from_env() == set()
    monkeypatch.delenv(ari.RESERVED_IDS_ENV, raising=False)
    assert ari.reserved_ids_from_env() == set()


def test_used_ids_folds_tree_main_and_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ari, "working_tree_ids", lambda: {1})
    monkeypatch.setattr(ari, "ids_on_git_ref", lambda ref: {2} if ref == "origin/main" else set())
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "5 7")
    assert ari.used_ids() == {1, 2, 5, 7}


def test_lower_pr_ids_from_env_parses_and_empties(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ari.LOWER_PR_IDS_ENV, "BE-0056 58")  # tokens and bare numbers both parse
    assert ari.lower_pr_ids_from_env() == {56, 58}
    monkeypatch.delenv(ari.LOWER_PR_IDS_ENV, raising=False)
    assert ari.lower_pr_ids_from_env() == set()


# --- collision detection ---------------------------------------------------------


def test_colliding_items_flags_introduced_id(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0054-operational-logging")  # new slug, id taken on main
    _make_item(roadmap, "proposals", "BE-0040-untouched")  # slug already on main -> inherited
    _make_item(roadmap, "proposals", "BE-0099-brand-new")  # id free on main
    main_ids = {54: "web-backend-completion", 40: "untouched"}
    assert ari.colliding_items(roadmap, main_ids) == [
        (54, "operational-logging", roadmap / "proposals" / "BE-0054-operational-logging")
    ]


def test_colliding_items_ignores_stale_inherited_item(tmp_path: Path) -> None:
    """A branch behind main — whose item main has since renumbered — is a rebase, not a renumber.

    The branch still carries operational-logging at its old BE-0054; main has since moved that
    item to BE-0055 and given BE-0054 to a different item. The slug is on main, so this is an
    inherited (stale) item, not one the branch introduces — it must not be renumbered.
    """
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0054-operational-logging")
    main_ids = {54: "web-backend-completion", 55: "operational-logging"}
    assert ari.colliding_items(roadmap, main_ids) == []


def test_colliding_items_empty_when_no_main(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0054-foo")
    assert ari.colliding_items(roadmap, {}) == []


def test_colliding_items_flags_lower_pr_collision(tmp_path: Path) -> None:
    """Nothing on main, so the number is contested only between open PRs: the lower PR number wins.

    This branch introduces dogfood-web-ui at BE-0056; a lower-numbered open PR also holds 0056
    (``lower_pr_ids``), so this branch is the loser and must renumber. Drop the lower claimant and
    this branch is itself the authority — no collision.
    """
    roadmap = tmp_path / "roadmaps"
    item = _make_item(roadmap, "implemented", "BE-0056-dogfood-web-ui")
    assert ari.colliding_items(roadmap, {}, lower_pr_ids={56}) == [(56, "dogfood-web-ui", item)]
    assert ari.colliding_items(roadmap, {}, lower_pr_ids=set()) == []


def test_colliding_items_lower_pr_ignores_inherited_slug(tmp_path: Path) -> None:
    """A lower PR sharing the number does not renumber an item whose slug is already on main.

    Slug on main means the branch inherited the item (a rebase resolves it); the lower-PR tiebreaker
    must not override that guard, or a stale branch would be renumbered instead of rebased.
    """
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0056-operational-logging")
    main_ids = {55: "operational-logging"}  # same slug on main at a different id -> inherited
    assert ari.colliding_items(roadmap, main_ids, lower_pr_ids={56}) == []


# --- ids_to_slugs_on_git_ref over a real ref -------------------------------------


def test_ids_to_slugs_reads_ref_and_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0007-alpha")
    _make_item(roadmap, "implemented", "BE-0003-beta")
    _git_init(tmp_path, monkeypatch)
    subprocess.run(["git", "commit", "-m", "seed", "-q"], cwd=tmp_path, check=True)
    assert ari.ids_to_slugs_on_git_ref("HEAD") == {7: "alpha", 3: "beta"}
    assert ari.ids_on_git_ref("HEAD") == {7, 3}
    # An unavailable ref degrades to empty, so a shallow/remoteless checkout still runs.
    assert ari.ids_to_slugs_on_git_ref("no-such-ref") == {}


# --- allocate (end-to-end over a throwaway git repo) -----------------------------


def test_allocate_honours_reserved_and_rewrites_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-XXXX-demo-feature")  # the placeholder to allocate
    _make_item(roadmap, "proposals", "BE-0003-existing")  # floor from the working tree
    (roadmap / "README.md").write_text(
        "| [demo](proposals/BE-XXXX-demo-feature/BE-XXXX-demo-feature.md) | Proposal |\n",
        encoding="utf-8",
    )
    (roadmap / "README-ja.md").write_text("no rows here\n", encoding="utf-8")
    _git_init(tmp_path, monkeypatch)
    # origin/main is unavailable (no remote) -> {}; reserved says 4 and 5 are taken by open PRs,
    # so the next free id above max(3,4,5) is 6.
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "4 5")

    assert ari.main([]) == 0

    allocated = roadmap / "proposals" / "BE-0006-demo-feature" / "BE-0006-demo-feature.md"
    assert allocated.is_file()
    assert not (roadmap / "proposals" / "BE-XXXX-demo-feature").exists()
    assert "BE-0006" in allocated.read_text(encoding="utf-8")
    assert "BE-XXXX" not in allocated.read_text(encoding="utf-8")
    # The index row that named the placeholder path is renumbered (path + link text).
    index = (roadmap / "README.md").read_text(encoding="utf-8")
    assert "BE-0006-demo-feature" in index and "BE-XXXX" not in index


def test_allocate_noop_without_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0003-existing")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(ari, "git_mv", lambda _s, _d: calls.append("mv"))
    assert ari.main([]) == 0
    assert calls == []


# --- repair (end-to-end over a throwaway git repo) -------------------------------


def test_repair_renumbers_collision_and_fixes_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0054-operational-logging")  # collides with main's 0054
    _make_item(
        roadmap,
        "proposals",
        "BE-0015-web-ui",
        body="\nSee [BE-0054](../BE-0054-operational-logging/BE-0054-operational-logging.md).\n",
    )
    _git_init(tmp_path, monkeypatch)
    # main now hands 0054 to a *different* item; the index rebuild is a no-op in this fixture.
    monkeypatch.setattr(ari, "ids_to_slugs_on_git_ref", lambda _ref: {54: "web-backend-completion"})
    monkeypatch.setattr(ari, "regenerate_index", lambda: None)

    remaps = ari.repair()

    assert remaps == [("BE-0054", "BE-0055")]  # next free above max(54, 15)
    new_dir = roadmap / "proposals" / "BE-0055-operational-logging"
    assert (new_dir / "BE-0055-operational-logging.md").is_file()
    assert not (roadmap / "proposals" / "BE-0054-operational-logging").exists()
    # The moved item's own self-references were rewritten.
    assert "BE-0055" in (new_dir / "BE-0055-operational-logging.md").read_text(encoding="utf-8")
    # A cross-reference whose line named the slug-qualified path is fully renumbered (path + text).
    cross = (roadmap / "proposals" / "BE-0015-web-ui" / "BE-0015-web-ui.md").read_text(
        encoding="utf-8"
    )
    assert "BE-0055-operational-logging" in cross
    assert "[BE-0055]" in cross
    assert "BE-0054" not in cross


def test_repair_renumbers_open_pr_collision_by_pr_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A number contested only between open PRs (nothing merged): the lower PR keeps it, this moves.

    The branch introduces BE-0056-dogfood-web-ui; main holds no 0056, but a lower-numbered open PR
    does (``ROADMAP_LOWER_PR_IDS``), so this branch is the loser. The next free id avoids every open
    PR's number (``ROADMAP_RESERVED_IDS``) — proving the move is driven purely by the open-PR
    tiebreaker, with main empty.
    """
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "implemented", "BE-0056-dogfood-web-ui")
    _git_init(tmp_path, monkeypatch)
    monkeypatch.setattr(ari, "ids_to_slugs_on_git_ref", lambda _ref: {})  # nothing on main
    monkeypatch.setattr(ari, "regenerate_index", lambda: None)
    monkeypatch.setenv(ari.LOWER_PR_IDS_ENV, "56")  # a lower-numbered open PR also holds 0056
    monkeypatch.setenv(ari.RESERVED_IDS_ENV, "56 59")  # other open PRs hold 0056 and 0059

    remaps = ari.repair()

    assert remaps == [("BE-0056", "BE-0060")]  # next free above max(56, 59)
    new_dir = roadmap / "implemented" / "BE-0060-dogfood-web-ui"
    assert (new_dir / "BE-0060-dogfood-web-ui.md").is_file()
    assert not (roadmap / "implemented" / "BE-0056-dogfood-web-ui").exists()
    assert "BE-0060" in (new_dir / "BE-0060-dogfood-web-ui.md").read_text(encoding="utf-8")


def test_repair_keeps_id_when_lowest_open_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lowest open-PR holder of a number is the authority and is never renumbered.

    Same contested 0056 as above, but no *lower*-numbered PR holds it (empty ``LOWER_PR_IDS``), so
    this branch is the authority and keeps the number — repair is a no-op even though other open PRs
    (higher-numbered) share it.
    """
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "implemented", "BE-0056-web-ui-aws-sso-login")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ari, "ids_to_slugs_on_git_ref", lambda _ref: {})  # nothing on main
    monkeypatch.setenv(
        ari.RESERVED_IDS_ENV, "58 59"
    )  # higher-numbered open PRs share nothing lower
    calls: list[str] = []
    monkeypatch.setattr(ari, "git_mv", lambda _s, _d: calls.append("mv"))
    monkeypatch.setattr(ari, "regenerate_index", lambda: calls.append("index"))
    assert ari.main(["--repair"]) == 0
    assert calls == []


def test_repair_noop_when_no_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "proposals", "BE-0054-operational-logging")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ari, "ids_to_slugs_on_git_ref", lambda _ref: {54: "operational-logging"})
    calls: list[str] = []
    monkeypatch.setattr(ari, "git_mv", lambda _s, _d: calls.append("mv"))
    monkeypatch.setattr(ari, "regenerate_index", lambda: calls.append("index"))
    assert ari.main(["--repair"]) == 0
    assert calls == []


# --- the gate --------------------------------------------------------------------


def test_committed_tree_has_no_duplicate_be_ids() -> None:
    """No two committed items share a BE number — the invariant allocate + repair protect."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    seen: dict[int, str] = {}
    duplicates: list[tuple[int, str, str]] = []
    for category in ari.CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            if not (d.is_dir() and (m := ari.NUMBERED_DIR_RE.match(d.name))):
                continue
            be_id = int(m.group(1))
            if be_id in seen:
                duplicates.append((be_id, seen[be_id], d.name))
            else:
                seen[be_id] = d.name
    assert duplicates == [], f"duplicate BE IDs in roadmaps/: {duplicates}"
