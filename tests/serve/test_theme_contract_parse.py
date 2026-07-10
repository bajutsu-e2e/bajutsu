"""Unit tests for theme contract parsing (BE-0191 unit 6)."""

from __future__ import annotations

from bajutsu.serve import themes


def test_parse_theme_tokens_color_extraction():
    """Extracting color tokens from CSS rules."""
    css = """:root {
      --bg: #fff;
      --card: #f0f0f0;
      --fg: #000;
      --mut: #888;
      --on-mut: #fff;
    }"""
    result = themes.parse_theme_tokens(css)
    assert "--bg" in result["colors"]
    assert "--card" in result["colors"]
    assert "--fg" in result["colors"]
    assert "--mut" in result["colors"]
    assert "--on-mut" in result["colors"]
    assert len(result["transitions"]) == 0


def test_parse_theme_tokens_motion_extraction():
    """Extracting motion tokens from CSS rules."""
    css = """:root {
      --motion-view: 0.18s;
      --motion-modal: 0.16s;
      --motion-ease: cubic-bezier(.4,0,.2,1);
      --motion-view-enter: bj-view-in;
      --motion-view-leave: bj-view-out;
    }"""
    result = themes.parse_theme_tokens(css)
    assert "--motion-view" in result["transitions"]
    assert "--motion-modal" in result["transitions"]
    assert "--motion-ease" in result["transitions"]
    assert "--motion-view-enter" in result["transitions"]
    assert "--motion-view-leave" in result["transitions"]
    assert len(result["colors"]) == 0
    # Infer type.
    assert result["transitions"]["--motion-ease"]["type"] == "easing"
    assert result["transitions"]["--motion-view-enter"]["type"] == "keyframe"
    assert result["transitions"]["--motion-modal"]["type"] == "duration"


def test_parse_theme_tokens_mixed():
    """Parsing mixed color and motion tokens."""
    css = """:root {
      --bg: #0f172a;
      --motion-view: 0.18s;
      --fg: #e2e8f0;
    }"""
    result = themes.parse_theme_tokens(css)
    assert "--bg" in result["colors"]
    assert "--fg" in result["colors"]
    assert "--motion-view" in result["transitions"]
    assert len(result["colors"]) == 2
    assert len(result["transitions"]) == 1


def test_parse_theme_tokens_empty_css():
    """Parsing a CSS with no tokens returns empty dicts."""
    result = themes.parse_theme_tokens("/* just a comment */")
    assert result["colors"] == {}
    assert result["transitions"] == {}
