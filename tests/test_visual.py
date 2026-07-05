"""Tests for visual regression image comparison."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from bajutsu.scenario import ExcludeRegion
from bajutsu.visual import compare_images


def _solid(color: tuple[int, ...], size: tuple[int, int] = (100, 100)) -> Image.Image:
    return Image.new("RGBA", size, color)


def _save(img: Image.Image, path: Path) -> Path:
    img.save(path)
    return path


def test_identical_images_pass(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((255, 0, 0, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline)
    assert result.ok
    assert result.diff_pct == 0.0


def test_different_images_fail(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((0, 0, 255, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline)
    assert not result.ok
    assert result.diff_pct > 0


def test_threshold_allows_small_diff(tmp_path: Path) -> None:
    # 1 pixel different out of 100x100 = 0.01%
    img = _solid((255, 0, 0, 255))
    img.putpixel((0, 0), (0, 0, 0, 255))
    actual = _save(img, tmp_path / "actual.png")
    baseline = _save(_solid((255, 0, 0, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline, threshold=0.1)
    assert result.ok
    assert result.diff_pct > 0


def test_exclude_region_masks_diff(tmp_path: Path) -> None:
    # Images differ only in the excluded region
    base = _solid((255, 0, 0, 255), (100, 100))
    changed = _solid((255, 0, 0, 255), (100, 100))
    for x in range(50):
        for y in range(10):
            changed.putpixel((x, y), (0, 255, 0, 255))  # top-left block differs
    actual = _save(changed, tmp_path / "actual.png")
    baseline = _save(base, tmp_path / "baseline.png")
    exclude = [ExcludeRegion(x=0, y=0, w=50, h=10)]
    result = compare_images(actual, baseline, exclude=exclude)
    assert result.ok
    assert result.diff_pct == 0.0


def test_diff_image_written(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((0, 0, 255, 255)), tmp_path / "baseline.png")
    diff_path = tmp_path / "diff.png"
    result = compare_images(actual, baseline, diff_path=diff_path)
    assert not result.ok
    assert diff_path.exists()
    diff_img = Image.open(diff_path)
    assert diff_img.size == (100, 100)


def test_size_mismatch_fails(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255), (100, 100)), tmp_path / "actual.png")
    baseline = _save(_solid((255, 0, 0, 255), (200, 200)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline)
    assert not result.ok
    assert "size" in result.reason


# --- exact engine explicit (BE-0165) ---


def test_exact_engine_explicit_identical(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((255, 0, 0, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline, engine="exact")
    assert result.ok
    assert result.diff_pct == 0.0


def test_exact_engine_explicit_different(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((0, 0, 255, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline, engine="exact")
    assert not result.ok


# --- byte pre-check short-circuit (BE-0165 item 4) ---


def test_byte_precheck_shortcircuits(tmp_path: Path) -> None:
    actual = _save(_solid((100, 100, 100, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((100, 100, 100, 255)), tmp_path / "baseline.png")
    for eng in ("exact", "pixelmatch"):
        result = compare_images(actual, baseline, engine=eng)
        assert result.ok
        assert result.diff_pct == 0.0


# --- pixelmatch engine (BE-0165) ---


def test_pixelmatch_identical(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((255, 0, 0, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline, engine="pixelmatch")
    assert result.ok
    assert result.diff_pct == 0.0


def test_pixelmatch_subpixel_noise_passes(tmp_path: Path) -> None:
    """Tiny RGB shifts within colorTolerance pass pixelmatch but fail exact."""
    baseline_img = _solid((200, 100, 50, 255))
    actual_img = _solid((200, 100, 50, 255))
    for x in range(10):
        actual_img.putpixel((x, 0), (202, 101, 51, 255))

    actual = _save(actual_img, tmp_path / "actual.png")
    baseline = _save(baseline_img, tmp_path / "baseline.png")

    exact = compare_images(actual, baseline, engine="exact")
    assert not exact.ok

    pm = compare_images(actual, baseline, engine="pixelmatch", color_tolerance=0.1)
    assert pm.ok


def test_pixelmatch_large_diff_fails(tmp_path: Path) -> None:
    actual = _save(_solid((255, 0, 0, 255)), tmp_path / "actual.png")
    baseline = _save(_solid((0, 0, 255, 255)), tmp_path / "baseline.png")
    result = compare_images(actual, baseline, engine="pixelmatch", color_tolerance=0.1)
    assert not result.ok
    assert result.diff_pct > 50


def test_pixelmatch_antialiasing_edge_shift(tmp_path: Path) -> None:
    """A black/white edge shifted by 1px is discounted with antialiasing=True."""
    size = (20, 20)
    baseline_img = Image.new("RGBA", size, (255, 255, 255, 255))
    actual_img = Image.new("RGBA", size, (255, 255, 255, 255))

    for y in range(20):
        for x in range(10):
            baseline_img.putpixel((x, y), (0, 0, 0, 255))
        for x in range(11):
            actual_img.putpixel((x, y), (0, 0, 0, 255))

    actual = _save(actual_img, tmp_path / "actual.png")
    baseline = _save(baseline_img, tmp_path / "baseline.png")

    with_aa = compare_images(
        actual, baseline, engine="pixelmatch", antialiasing=True, threshold=5.0
    )
    assert with_aa.ok

    without_aa = compare_images(
        actual, baseline, engine="pixelmatch", antialiasing=False, threshold=0.0
    )
    assert not without_aa.ok


def test_pixelmatch_diff_image(tmp_path: Path) -> None:
    """pixelmatch diff highlights surviving (non-discounted) different pixels."""
    actual_img = _solid((255, 0, 0, 255), (10, 10))
    baseline_img = _solid((0, 0, 255, 255), (10, 10))
    actual = _save(actual_img, tmp_path / "actual.png")
    baseline = _save(baseline_img, tmp_path / "baseline.png")
    diff_path = tmp_path / "diff.png"

    result = compare_images(actual, baseline, engine="pixelmatch", diff_path=diff_path)
    assert not result.ok
    assert diff_path.exists()
    diff_img = Image.open(diff_path)
    assert diff_img.size == (10, 10)
    r, _g, _b, _a = diff_img.getpixel((5, 5))
    assert r > 200
