"""One external command that runs around a run or a scenario (BE-0491, BE-0501).

The entry model a target's four `hooks` lists and a scenario's two share. It lives beside the
scenario models because the scenario schema may not import `config`, while `config` already reads
the scenario's. A command is an exit code and an output file, never a judgment: what passes stays
the scenario's machine-checked assertions (prime directive 1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.tags import tags_match

# An entry's `name`, and every key it writes to `$BAJUTSU_OUTPUT`. `__` separates the two halves of
# the `BAJUTSU_HOOK__<name>__<key>` variable a later command reads, so neither half may contain it.
HOOK_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Stands where a target's name goes in the log path of a scenario's own entries (BE-0501). `config`
# refuses a target of this name, so the two levels never share a path.
SCENARIO_LEVEL = "_scenario"

# The outcome an `afterRun` / `afterScenario` entry answers, in the words a scenario's `cleanup`
# rules already use (BE-0392), so one vocabulary names a verdict everywhere.
HookOutcome = Literal["always", "success", "failure"]


def invalid_hook_name(value: str) -> str | None:
    """Why *value* cannot name a hook entry or one of its output keys, or None.

    Shared with the runner, which checks each key a command writes at run time against the same
    rule the schema applies to `name` at load time.
    """
    if not HOOK_NAME.match(value):
        return f"{value!r} must start with a letter and hold only letters, digits, and underscores"
    if "__" in value:
        return (
            f"{value!r} contains '__', which separates the halves of the BAJUTSU_HOOK__<name>__<key>"
            " variable a later command reads"
        )
    return None


class HookWhen(_Model):
    """`when`: the scenario tags a hook entry runs for (BE-0497).

    The rule is the one `--tag` and `--exclude` apply to selection, through the same predicate.
    """

    tags: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _constrains_something(self) -> Self:
        if not self.tags and not self.exclude:
            raise ValueError(
                "a hook entry's `when` needs a non-empty `tags` or `exclude`; leave `when` out for "
                "an entry that always runs"
            )
        return self

    def matches(self, tags: Iterable[str]) -> bool:
        """Whether a scenario carrying *tags* meets this condition."""
        return tags_match(tags, self.tags, self.exclude)

    def __str__(self) -> str:
        """The condition as reports and errors word it, listing only the fields that constrain."""
        parts = [
            f"{field}: [{', '.join(values)}]"
            for field, values in (("tags", self.tags), ("exclude", self.exclude))
            if values
        ]
        return ", ".join(parts)


class HookCommand(_Model):
    """One entry of a `hooks.beforeRun` / `afterRun` / `beforeScenario` / `afterScenario` list.

    An entry names a command line (`cmd`) or a script the worker installed (`use`, BE-0492), never
    both. It runs to completion, under a bounded `timeout`, and writes its values as `key=value`
    lines to the file `$BAJUTSU_OUTPUT` names; a scenario then reads one as
    `${hooks.<name>.<key>}`. Readiness belongs to the command: a script that starts a server exits
    once the server answers, picking whatever check that server needs.
    """

    cmd: str | None = None
    # The name of a command the worker lists in `namedCommands` (BE-0492), whose script the operator
    # vouches for; `env` may then set only the variables that entry lists, and `cwd` is ignored.
    use: str | None = None
    # Required on an entry whose outputs a scenario or a later command reads, and unique across the
    # four lists of every target a run drives, so a `${hooks.<name>.<key>}` token is unambiguous.
    name: str | None = None
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = 300.0
    # Only an `after` list reads this; `Hooks` rejects it on a `before` list, where no verdict exists
    # yet to answer.
    on: HookOutcome = "always"
    # Masks this entry's output values in evidence, as a `${secrets.*}` value already is.
    mask: bool = False
    # Runs the entry only for a scenario whose tags match; absent, it runs for every scenario.
    when: HookWhen | None = None

    @model_validator(mode="after")
    def _one_command(self) -> Self:
        if (self.cmd is None) == (self.use is None):
            raise ValueError(
                "a hook entry needs exactly one of `cmd` (a command line) or `use` (a command the "
                f"worker lists in namedCommands), got {'both' if self.cmd else 'neither'}"
            )
        if "when" in self.model_fields_set and self.when is None:
            # `when:` with its body commented out would otherwise widen the entry to every scenario.
            raise ValueError("a hook entry's `when` needs a non-empty `tags` or `exclude`")
        if self.cmd is not None and not self.cmd.strip():
            raise ValueError("a hook entry's `cmd` cannot be empty")
        if self.name is not None and (why := invalid_hook_name(self.name)) is not None:
            raise ValueError(f"hook `name` {why}")
        if self.timeout <= 0:
            raise ValueError(f"a hook entry's `timeout` must be positive, got {self.timeout}")
        return self

    @property
    def label(self) -> str:
        """How reports and failure messages name this entry: its `name`, else its command."""
        return self.name or (self.cmd if self.cmd is not None else f"use:{self.use}")


def reject_outcome_on_before(entries: Iterable[HookCommand], where: str) -> None:
    """Fail a `before` entry that sets `on`, which has no verdict to answer yet.

    Raises:
        ValueError: An entry of *entries* sets `on`.
    """
    for i, entry in enumerate(entries):
        if "on" in entry.model_fields_set:
            raise ValueError(
                f"{where}[{i}] cannot set `on`: no verdict exists before the thing it precedes, so "
                "`on` belongs to afterRun / afterScenario"
            )


def reject_run_level_exclude_only(entries: Iterable[HookCommand], where: str) -> None:
    """Fail a run-level entry whose `when` names no `tags`.

    Such a condition skips only when *every* scenario of the run carries an excluded tag, which is
    not what an author who writes it means (BE-0497).
    """
    for i, entry in enumerate(entries):
        if entry.when is not None and not entry.when.tags:
            raise ValueError(
                f"{where}[{i}].when needs a non-empty `tags`: a run-level entry runs when any "
                "scenario of the run matches, so `exclude` alone would almost never skip it"
            )
