"""Tests for the minimal .env loader (pure parse + non-overriding load)."""

from __future__ import annotations

from pathlib import Path

from bajutsu.dotenv import load_dotenv, parse_dotenv


def test_parse_basic_pairs() -> None:
    parsed = parse_dotenv("A=1\nB=two\n")
    assert parsed == {"A": "1", "B": "two"}


def test_parse_skips_blanks_and_comments() -> None:
    parsed = parse_dotenv("\n# a comment\nKEY=value\n   \n#another\n")
    assert parsed == {"KEY": "value"}


def test_parse_strips_export_and_quotes() -> None:
    parsed = parse_dotenv("export TOKEN=\"sk-ant-xyz\"\nNAME='Jane Doe'\n")
    assert parsed == {"TOKEN": "sk-ant-xyz", "NAME": "Jane Doe"}


def test_parse_keeps_value_equals_and_trims_space() -> None:
    parsed = parse_dotenv("  URL = https://x/?a=1&b=2  \n")
    assert parsed == {"URL": "https://x/?a=1&b=2"}


def test_parse_ignores_lines_without_assignment() -> None:
    parsed = parse_dotenv("not_an_assignment\n=missingkey\nGOOD=1\n")
    assert parsed == {"GOOD": "1"}


def test_load_sets_only_missing_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\nNEW=added\n", encoding="utf-8")
    environ: dict[str, str] = {"ANTHROPIC_API_KEY": "from-real-env"}
    applied = load_dotenv(env_file, environ)
    assert applied == ["NEW"]  # a non-empty real value is left untouched
    assert environ["ANTHROPIC_API_KEY"] == "from-real-env"
    assert environ["NEW"] == "added"


def test_load_fills_empty_env_var(tmp_path: Path) -> None:
    # Some environments preset ANTHROPIC_API_KEY to an empty string; treat it as unset.
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    environ: dict[str, str] = {"ANTHROPIC_API_KEY": ""}
    assert load_dotenv(env_file, environ) == ["ANTHROPIC_API_KEY"]
    assert environ["ANTHROPIC_API_KEY"] == "from-file"


def test_load_missing_file_is_noop(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    assert load_dotenv(tmp_path / "nope.env", environ) == []
    assert environ == {}
