"""Tests for `step_view` — the one screenshot and one tree a viewer shows for a step.

Every consumer that shows a step's evidence resolves it here (the HTML report's element viewer, the
serve editor's picker, the triage context), so the pairing rule lives in one place: an image and a
tree go together only when they describe the same screen, and a viewer that would draw element
frames onto pixels those frames never described is told not to.
"""

from __future__ import annotations

from bajutsu.common.evidence import StepView, step_view


def _entries(*rows: tuple[str, str, str | None]) -> list[tuple[str, str, str | None]]:
    return list(rows)


def test_pairs_the_screenshot_that_depicts_the_trees_screen() -> None:
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("elements", "s/elements.json", "adb:before"),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "adb:after"),
        )
    )
    assert view == StepView("s/after.png", "s/elements.json", True)


def test_takes_the_last_tree_entry_because_that_write_is_the_one_that_survives() -> None:
    # A step records two `elements` entries under one filename: the pre-step baseline's, then the
    # post-step write that replaces it. Only the last entry describes what the file now holds, so a
    # step that never reached its post-step write pairs with `before.png` instead.
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("elements", "s/elements.json", "adb:before"),
        )
    )
    assert (view.screenshot, view.paired) == ("s/before.png", True)


def test_reports_a_web_blocks_native_image_and_webview_tree_as_unpaired() -> None:
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "webview:after"),
        )
    )
    # The image is still offered — it is the only picture of the step there is — but the caller is
    # told it does not describe the tree, so no frame is drawn on it.
    assert (view.screenshot, view.elements, view.paired) == (
        "s/after.png",
        "s/elements.json",
        False,
    )


def test_falls_back_unpaired_when_the_matching_screenshot_is_gone() -> None:
    # A manifest can name a file the store no longer holds (a run restored from Trash, or one synced
    # into an object store that never received the last write). `before.png` is then the only image
    # left, and it does not describe the post-action tree beside it.
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "adb:after"),
        ),
        exists=lambda name: not name.endswith("after.png"),
    )
    assert (view.screenshot, view.paired) == ("s/before.png", False)


def test_probes_existence_lazily_in_preference_order() -> None:
    probed: list[str] = []

    def exists(name: str) -> bool:
        probed.append(name)
        return True

    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "adb:after"),
        ),
        exists=exists,
    )
    # One probe, on the one candidate that matched: this predicate is a live object-store lookup on
    # the hosted backend, and a scenario read walks every step.
    assert (view.screenshot, probed) == ("s/after.png", ["s/after.png"])


def test_a_run_recorded_before_depicts_existed_keeps_its_old_choice() -> None:
    # Nothing in such a manifest says which side of the action an artifact was taken on, so the
    # result reproduces the pre-field choice — `after.png` over the `before.png` beside it — and
    # reports it as paired: a stored run keeps the frames it has always drawn.
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", None),
            ("screenshot", "s/after.png", None),
            ("elements", "s/elements.json", None),
        )
    )
    assert (view.screenshot, view.elements, view.paired) == (
        "s/after.png",
        "s/elements.json",
        True,
    )


def test_a_legacy_run_without_after_png_falls_back_to_the_first_screenshot() -> None:
    view = step_view(_entries(("screenshot", "s/shot.png", None), ("elements", "s/e.json", None)))
    assert (view.screenshot, view.paired) == ("s/shot.png", True)


def test_a_step_whose_screenshots_the_store_lost_is_not_a_mismatch() -> None:
    # Every recorded screenshot is gone from the store, so the fallback finds none. There is no
    # image left to mispair, and a viewer told "these describe different screens" would state a
    # reason that is not the reason its frames are absent (review follow-up).
    view = step_view(
        _entries(
            ("screenshot", "s/before.png", "adb:before"),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "adb:after"),
        ),
        exists=lambda _name: False,
    )
    assert (view.screenshot, view.elements, view.paired) == (None, "s/elements.json", True)


def test_a_step_with_no_screenshot_is_not_a_mismatch() -> None:
    # Nothing to mispair: the tree stands on its own, and a viewer with no image draws no frames.
    view = step_view(_entries(("elements", "s/elements.json", "adb:after")))
    assert (view.screenshot, view.elements, view.paired) == (None, "s/elements.json", True)


def test_a_step_with_no_artifacts_resolves_to_nothing() -> None:
    view = step_view([])
    assert (view.screenshot, view.elements, view.paired) == (None, None, True)


def test_ignores_kinds_that_show_no_screen() -> None:
    view = step_view(
        _entries(
            ("video", "s/scenario.mp4", None),
            ("waitDiagnostic", "s/wait-timeout.json", None),
            ("screenshot", "s/after.png", "adb:after"),
            ("elements", "s/elements.json", "adb:after"),
        )
    )
    assert (view.screenshot, view.elements, view.paired) == (
        "s/after.png",
        "s/elements.json",
        True,
    )
