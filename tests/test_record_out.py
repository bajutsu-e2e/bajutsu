"""Tests for where `record` writes, and the read-only-Git-source guard (BE-0063)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.cli._shared import _refuse_out_in_checkout
from bajutsu.cli.commands.record import _record_out_path, _secret_tokens
from bajutsu.config import load_config, resolve


def _eff(scenarios: str):  # type: ignore[no-untyped-def]
    cfg = load_config(f"targets:\n  x:\n    bundleId: com.x\n    scenarios: {scenarios}\n")
    return resolve(cfg, "x")


def test_record_no_out_and_no_scenarios_dir_exits_2() -> None:
    # With neither --out nor a configured scenarios dir there is nowhere to auto-name: a usage
    # error (exit 2), not a guess.
    eff = resolve(load_config("targets:\n  x:\n    bundleId: com.x\n"), "x")
    with pytest.raises(typer.Exit) as exc:
        _record_out_path(eff, "", "name", "goal", "x", checkout_root=None)
    assert exc.value.exit_code == 2


def test_record_out_explicit_path_is_used_as_is(tmp_path: Path) -> None:
    out = tmp_path / "scn.yaml"
    assert _record_out_path(_eff("e2e"), str(out), "name", "goal", "x", checkout_root=None) == out


def test_record_auto_names_under_the_configured_dir_for_a_local_config() -> None:
    # A local config keeps today's behavior: the auto-named file lands under the configured dir.
    p = _record_out_path(_eff("scn/dir"), "", "login", "goal", "x", checkout_root=None)
    assert p.parent == Path("scn/dir")


def test_record_auto_names_under_cwd_for_a_git_source(tmp_path: Path) -> None:
    # A Git source is read-only: with no --out the recorded scenario defaults under the current
    # directory, NOT under the configured scenarios dir (which is inside the SHA-keyed cache).
    checkout = tmp_path / "cache" / "co"
    eff = _eff(str(checkout / "e2e"))  # configured dir is inside the checkout
    p = _record_out_path(eff, "", "login", "goal", "x", checkout_root=checkout)
    assert not p.resolve().is_relative_to(checkout.resolve())  # not written into the cache
    assert p.parent == Path()  # under cwd


def test_record_out_inside_the_checkout_is_refused(tmp_path: Path) -> None:
    checkout = tmp_path / "co"
    inside = checkout / "e2e" / "scn.yaml"
    with pytest.raises(typer.Exit) as exc:
        _record_out_path(_eff("e2e"), str(inside), "n", "g", "x", checkout_root=checkout)
    assert exc.value.exit_code == 2


def test_refuse_out_in_checkout_allows_local_and_no_source(tmp_path: Path) -> None:
    # No Git source → never refuses. A path outside the checkout → allowed.
    _refuse_out_in_checkout(tmp_path / "x.yaml", None)
    _refuse_out_in_checkout(tmp_path / "out" / "x.yaml", tmp_path / "co")
    with pytest.raises(typer.Exit) as exc:
        _refuse_out_in_checkout(tmp_path / "co" / "x.yaml", tmp_path / "co")
    assert exc.value.exit_code == 2  # the exit-2 CLI contract used across this file


def test_record_auto_name_inside_the_checkout_is_refused(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # If `record` is run from inside the materialized checkout (cwd under it), even the auto-named
    # (no --out) path would land in the read-only cache — it is refused, not silently written.
    checkout = tmp_path / "co"
    (checkout / "sub").mkdir(parents=True)
    monkeypatch.chdir(checkout / "sub")  # cwd is now inside the checkout
    with pytest.raises(typer.Exit) as exc:
        _record_out_path(_eff("e2e"), "", "n", "g", "x", checkout_root=checkout)
    assert exc.value.exit_code == 2


# --- _secret_tokens: resolving declared secrets from the environment (BE-0120) ---


def _eff_with_secrets(*names: str):  # type: ignore[no-untyped-def]
    decl = "".join(f"      - {n}\n" for n in names)
    return resolve(load_config(f"targets:\n  x:\n    bundleId: com.x\n    secrets:\n{decl}"), "x")


def test_secret_tokens_maps_set_env_vars_to_their_tokens(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PASSWORD", "hunter2")
    monkeypatch.delenv("API_KEY", raising=False)  # declared but unset: not bindable, so skipped
    assert _secret_tokens(_eff_with_secrets("PASSWORD", "API_KEY")) == [
        ("hunter2", "${secrets.PASSWORD}")
    ]


def test_secret_tokens_orders_longest_value_first(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Longest value first so a value that is a substring of another is substituted before it.
    monkeypatch.setenv("SHORT", "abc")
    monkeypatch.setenv("LONG", "abcdef")
    assert _secret_tokens(_eff_with_secrets("SHORT", "LONG")) == [
        ("abcdef", "${secrets.LONG}"),
        ("abc", "${secrets.SHORT}"),
    ]
