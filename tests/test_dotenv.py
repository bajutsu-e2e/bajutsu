"""Tests for the minimal .env loader (pure parse + non-overriding load)."""

from __future__ import annotations

from pathlib import Path

from bajutsu.dotenv import load_dotenv, parse_dotenv, upsert_dotenv


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


def test_upsert_creates_file_and_adds_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    upsert_dotenv("ANTHROPIC_API_KEY", "sk-ant-new", env_file)
    assert parse_dotenv(env_file.read_text(encoding="utf-8")) == {"ANTHROPIC_API_KEY": "sk-ant-new"}


def test_upsert_replaces_in_place_and_keeps_other_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# secrets\nANTHROPIC_API_KEY=old\nOTHER=keep\n", encoding="utf-8")
    upsert_dotenv("ANTHROPIC_API_KEY", "new", env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "# secrets" in text and "OTHER=keep" in text
    assert parse_dotenv(text) == {"ANTHROPIC_API_KEY": "new", "OTHER": "keep"}


def test_upsert_none_removes_key_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=old\nOTHER=keep\n", encoding="utf-8")
    upsert_dotenv("ANTHROPIC_API_KEY", None, env_file)
    assert parse_dotenv(env_file.read_text(encoding="utf-8")) == {"OTHER": "keep"}


def test_upsert_none_on_missing_file_is_noop(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    upsert_dotenv("ANTHROPIC_API_KEY", None, env_file)
    assert not env_file.exists()


def test_upsert_restricts_file_to_owner(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    upsert_dotenv("ANTHROPIC_API_KEY", "sk-ant-secret", env_file)
    assert env_file.stat().st_mode & 0o777 == 0o600  # owner read/write only
