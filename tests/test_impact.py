"""Tests for test impact analysis (bajutsu/analysis/impact.py, BE-0321).

Impact analysis is a pure, device-free, AI-free function of a scenario suite plus a `git` diff: it
inverts the suite's static references into `literal → step`, matches referenced literals against the
diff's changed lines, and reports the affected `(scenario, step)` pairs and a soundness signal. It
never runs a scenario and never decides pass/fail — over-selection is the safe direction, and a
change matching no referenced literal flags the report incomplete rather than silently narrowing.
"""

from __future__ import annotations

from bajutsu.analysis.impact import (
    ChangedFile,
    Reference,
    impact,
    parse_diff,
    render,
    reverse_index,
)
from bajutsu.scenario import load_scenarios


def _index(yaml: str):  # type: ignore[no-untyped-def]
    return reverse_index(load_scenarios(yaml))


# --- reverse index: step-granular id / screen / endpoint references ---


def test_reverse_index_maps_a_step_id_to_its_position_and_label() -> None:
    idx = _index("- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n")
    (steps,) = [v for k, v in idx.entries.items() if k == Reference("id", "login.button")]
    assert [(s.scenario, s.index, s.label) for s in steps] == [("login", 1, "tap login")]


def test_step_label_falls_back_to_the_action_key() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    (steps,) = [v for k, v in idx.entries.items() if k == Reference("id", "home.start")]
    assert steps[0].label == "tap"  # no `name:`, so the YAML action key labels it


def test_reverse_index_covers_nested_control_flow_and_within() -> None:
    idx = _index(
        "- name: x\n  steps:\n"
        "    - forEach:\n        sel: { id: row.item }\n        as: r\n"
        "        steps:\n          - tap: { id: row.open }\n"
    )
    # Both the forEach selector and the nested tap attribute to the top-level step (index 1).
    for id_ in ("row.item", "row.open"):
        (steps,) = [v for k, v in idx.entries.items() if k == Reference("id", id_)]
        assert steps[0].index == 1


def test_reverse_index_maps_every_id_candidate_spelling() -> None:
    # A BE-0221 OR-candidate id carries each platform's spelling; a change to *any* of them must be
    # matchable, so all candidates are indexed (not just the canonical dotted one).
    idx = _index("- name: x\n  steps:\n    - tap: { id: [login.button, login_button] }\n")
    id_keys = {k.value for k in idx.entries if k.kind == "id"}
    assert id_keys == {"login.button", "login_button"}


def test_reverse_index_does_not_index_idmatches_regex() -> None:
    # `idMatches` is a regex/pattern, not a literal that appears verbatim in source — like the endpoint
    # side dropping urlMatches/pathMatches, it must not be substring-matched against a diff.
    idx = _index("- name: x\n  steps:\n    - tap: { idMatches: 'login.*' }\n")
    assert not [k for k in idx.entries if k.kind == "id"]


def test_a_change_to_the_native_id_alternate_selects_the_step() -> None:
    # The under-selection the multi-candidate index fixes: source touches only the platform-native
    # spelling `login_button`, which must still select the step authored with the OR-candidate id.
    idx = _index(
        "- name: login\n  steps:\n"
        "    - { tap: { id: [login.button, login_button] }, name: tap login }\n"
    )
    report = impact(idx, [ChangedFile("Login.kt", ['resource-id = "login_button"'])])
    assert [a.step.label for a in report.affected] == ["tap login"]


def test_reverse_index_maps_endpoint_literals_not_regex() -> None:
    idx = _index(
        "- name: x\n  steps:\n"
        "    - assert: [ { request: { path: /api/session } } ]\n"
        "    - assert: [ { request: { pathMatches: /api/.* } } ]\n"
    )
    keys = {k for k in idx.entries if k.kind == "endpoint"}
    assert keys == {Reference("endpoint", "/api/session")}  # the regex form is not a literal


def test_reverse_index_maps_setup_and_deeplink_as_scenario_level_screens() -> None:
    idx = _index(
        "- name: x\n  preconditions: { setup: warm_cart, deeplink: 'app://home' }\n"
        "  steps:\n    - tap: { id: home.start }\n"
    )
    screens = {k.value: v for k, v in idx.entries.items() if k.kind == "screen"}
    assert set(screens) == {"warm_cart", "app://home"}
    assert all(v[0].index == 0 for v in screens.values())  # no single step owns them


def test_scenario_level_expect_id_attaches_to_a_scenario_level_ref() -> None:
    idx = _index(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n"
        "  expect:\n    - exists: { id: home.title }\n"
    )
    (steps,) = [v for k, v in idx.entries.items() if k == Reference("id", "home.title")]
    assert steps[0].index == 0 and steps[0].label == "expect"


# --- diff parsing ---


def test_parse_diff_collects_added_and_removed_lines_per_file() -> None:
    diff = (
        "diff --git a/A.swift b/A.swift\n"
        "index 111..222 100644\n"
        "--- a/A.swift\n"
        "+++ b/A.swift\n"
        "@@ -1,2 +1,2 @@\n"
        " context line\n"
        "-let old = 1\n"
        "+let new = 2\n"
    )
    (f,) = parse_diff(diff)
    assert f.path == "A.swift"
    assert f.lines == ["let old = 1", "let new = 2"]  # context line excluded, +/- prefixes stripped


def test_parse_diff_keys_a_deletion_by_its_old_path() -> None:
    diff = (
        "diff --git a/Gone.swift b/Gone.swift\n"
        "deleted file mode 100644\n"
        "--- a/Gone.swift\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-let removed = 1\n"
    )
    (f,) = parse_diff(diff)
    assert f.path == "Gone.swift" and f.lines == ["let removed = 1"]


def test_parse_diff_separates_multiple_files() -> None:
    diff = (
        "diff --git a/A b/A\n--- a/A\n+++ b/A\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/B b/B\n--- a/B\n+++ b/B\n@@ -1 +1 @@\n-c\n+d\n"
    )
    assert [(f.path, f.lines) for f in parse_diff(diff)] == [("A", ["a", "b"]), ("B", ["c", "d"])]


def test_parse_diff_empty_is_no_files() -> None:
    assert parse_diff("") == []


def test_parse_diff_collects_a_line_body_that_starts_with_a_plus() -> None:
    # An added line whose own text starts with `+`/`-` (`++counter`) must be collected, not mistaken
    # for a `+++`/`---` path header — hunk tracking distinguishes them.
    diff = (
        "diff --git a/A.c b/A.c\n--- a/A.c\n+++ b/A.c\n"
        "@@ -1,2 +1,2 @@\n-  --removed = 1\n+  ++counter = 2\n"
    )
    (f,) = parse_diff(diff)
    assert f.lines == ["  --removed = 1", "  ++counter = 2"]


def test_parse_diff_accumulates_across_multiple_hunks_in_one_file() -> None:
    diff = "diff --git a/A b/A\n--- a/A\n+++ b/A\n@@ -1 +1 @@\n-a\n+b\n@@ -9 +9 @@\n-c\n+d\n"
    (f,) = parse_diff(diff)
    assert f.lines == ["a", "b", "c", "d"]


def test_parse_diff_marks_a_binary_hunk() -> None:
    diff = "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
    (f,) = parse_diff(diff)
    assert f.path == "logo.png" and f.binary is True and f.lines == []


def test_parse_diff_keys_a_git_binary_patch_by_its_real_path() -> None:
    # A `GIT binary patch` section (git diff --binary) carries no +++/--- lines, so the path must come
    # from the `diff --git` header, not fall back to the literal "GIT binary patch".
    diff = (
        "diff --git a/icon.png b/icon.png\nindex a1..b2 100644\n"
        "GIT binary patch\ndelta 12\nzcmV0abcd\n"
    )
    (f,) = parse_diff(diff)
    assert f.path == "icon.png" and f.binary is True


# --- impact: touched set, affected steps, soundness ---


def test_a_touched_id_selects_only_the_referencing_step_with_its_reason() -> None:
    idx = _index(
        "- name: login\n  steps:\n"
        "    - { tap: { id: login.button }, name: tap login }\n"
        "    - { tap: { id: home.start }, name: go home }\n"
    )
    changed = [ChangedFile("Login.swift", ['id = "login.button"'])]
    report = impact(idx, changed)
    assert [a.step.label for a in report.affected] == ["tap login"]
    assert report.affected[0].reasons == [Reference("id", "login.button")]
    assert report.complete is True  # the one changed file matched a referenced literal


def test_one_touched_literal_selects_every_step_that_references_it() -> None:
    # The core inversion: a literal shared by two steps must select both from a single changed line.
    idx = _index(
        "- name: cart\n  steps:\n"
        "    - { tap: { id: cart.item }, name: open item }\n"
        "    - { assert: [ { exists: { id: cart.item } } ], name: check item }\n"
    )
    changed = [ChangedFile("Cart.swift", ['id = "cart.item"'])]
    report = impact(idx, changed)
    assert sorted(a.step.label for a in report.affected) == ["check item", "open item"]


def test_a_touched_endpoint_selects_the_asserting_step_end_to_end() -> None:
    idx = _index(
        "- name: api\n  steps:\n"
        "    - { assert: [ { request: { path: /api/session } } ], name: await session }\n"
    )
    changed = [ChangedFile("Router.swift", ['route("/api/session")'])]
    report = impact(idx, changed)
    assert [a.step.label for a in report.affected] == ["await session"]
    assert report.affected[0].reasons == [Reference("endpoint", "/api/session")]


def test_a_binary_change_is_unattributable_and_incomplete() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    report = impact(idx, [ChangedFile("logo.png", [], binary=True)])
    assert report.affected == []
    assert report.unattributable == ["logo.png"] and report.complete is False


def test_a_change_matching_no_literal_is_unattributable_and_incomplete() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    changed = [ChangedFile("Helper.swift", ["let x = compute()"])]
    report = impact(idx, changed)
    assert report.affected == []
    assert report.unattributable == ["Helper.swift"]
    assert report.complete is False  # a CI narrowing must fall back to the full suite


def test_a_file_touching_a_literal_alongside_other_lines_stays_attributed() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    changed = [ChangedFile("Home.swift", ["let unrelated = 1", 'id = "home.start"'])]
    report = impact(idx, changed)
    assert report.unattributable == [] and report.complete is True


def test_a_pure_rename_with_no_changed_lines_is_not_unattributable() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    report = impact(idx, [ChangedFile("Renamed.swift", [])])
    assert report.unattributable == [] and report.complete is True


def test_touched_reference_records_every_file_that_carries_it() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: shared.id }\n")
    changed = [
        ChangedFile("B.swift", ['"shared.id"']),
        ChangedFile("A.swift", ['"shared.id"']),
    ]
    report = impact(idx, changed)
    (touched,) = report.touched
    assert touched.reference == Reference("id", "shared.id")
    assert touched.files == ["A.swift", "B.swift"]  # sorted, de-duped


def test_empty_diff_selects_nothing_and_stays_complete() -> None:
    idx = _index("- name: x\n  steps:\n    - tap: { id: home.start }\n")
    report = impact(idx, [])
    assert report.affected == [] and report.unattributable == [] and report.complete is True


def test_render_lists_affected_steps_and_the_incomplete_signal() -> None:
    idx = _index("- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n")
    changed = [
        ChangedFile("Login.swift", ['"login.button"']),
        ChangedFile("Logic.swift", ["compute()"]),
    ]
    text = render(impact(idx, changed))
    assert "login > tap login (step 1)" in text
    assert "id:login.button" in text
    assert "incomplete" in text and "Logic.swift" in text


def test_render_omits_the_step_suffix_for_a_scenario_level_reference() -> None:
    # A scenario-level `expect` reference surfaces at index 0, so its rendered line carries no
    # `(step N)` suffix — the false branch of render's index gate.
    idx = _index(
        "- name: x\n  steps:\n    - tap: { id: home.start }\n"
        "  expect:\n    - exists: { id: home.title }\n"
    )
    text = render(impact(idx, [ChangedFile("Home.swift", ['"home.title"'])]))
    assert "x > expect" in text and "(step" not in text
