"""The Claude / Claude-free classification (BE-0101) covers every command, exactly once.

This is the MECE guard: the classification is only a trustworthy source of truth if it maps the
*whole* command set with no gaps or extras, so a newly added command forces a classification entry
(the test fails until one exists) and a removed one can't leave a stale entry behind.
"""

from __future__ import annotations

import typer.main

from bajutsu import capabilities
from bajutsu.cli import app


def _registered_commands() -> set[str]:
    return set(typer.main.get_command(app).commands.keys())


def test_classification_matches_the_registered_command_set_exactly() -> None:
    classified = {c.command for c in capabilities.CAPABILITIES}
    registered = _registered_commands()
    assert classified == registered, (
        "the Claude classification must cover exactly the registered commands "
        f"(missing: {sorted(registered - classified)}; extra: {sorted(classified - registered)})"
    )


def test_no_command_is_classified_twice() -> None:
    commands = [c.command for c in capabilities.CAPABILITIES]
    assert len(commands) == len(set(commands))


def test_claude_using_set_is_exactly_the_authoring_paths() -> None:
    # The two always-Claude commands; triage/run are Claude-free by default (flag-gated).
    assert set(capabilities.claude_using()) == {"record", "crawl"}


def test_claude_free_and_using_partition_every_command() -> None:
    free, using = set(capabilities.claude_free()), set(capabilities.claude_using())
    assert free.isdisjoint(using)
    assert free | using == _registered_commands()


def test_every_command_lands_in_a_claude_help_panel() -> None:
    # `bajutsu --help` groups by `capabilities` (BE-0101); the grouping derives the command name
    # independently of the click name, so guard end-to-end that every command got one of the two
    # boundary panels — an unassigned one keeps Typer's default placeholder (not a str), which would
    # leave it ungrouped rather than fail any other test.
    panels = {info.rich_help_panel for info in app.registered_commands}
    assert all(isinstance(p, str) for p in panels), "a command was left without a help panel"
    assert len(panels) == 2, f"expected exactly the two Claude-boundary panels, got {panels}"


def test_flag_gated_commands_name_the_flag_that_reaches_claude() -> None:
    # A Claude-free command with a Claude path behind a flag must record that flag, so the docs /
    # help can say `triage --ai` and `run --alert-handling` rather than mislabel the command.
    assert capabilities.by_command("triage").claude_flag == "--ai"
    assert capabilities.by_command("run").claude_flag == "--alert-handling"
    # An always-Claude command has no flip flag.
    assert capabilities.by_command("record").claude_flag is None
