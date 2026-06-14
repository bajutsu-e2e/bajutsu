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
