"""One external command that runs around a run or a scenario (BE-0491, BE-0501).

The entry model a target's four `hooks` lists and a scenario's two share. It lives beside the
scenario models because the scenario schema may not import `config`, while `config` already reads
the scenario's. A command is an exit code and an output file, never a judgment: what passes stays
the scenario's machine-checked assertions (prime directive 1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

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


# The platforms a `when.platforms` entry may name: a target's declared `platform` (BE-0513).
HookPlatform = Literal["ios", "android", "web", "fake"]

# The destinations a `when.routes` entry may name, the route names BE-0467 already gives each
# environment. Spelled here because the scenario models may not import `capability`, whose
# `settings_support.Route` lists the same names; a test keeps the two in step.
HookRoute = Literal[
    "ios-simulator", "ios-device", "ios-live", "android-emulator", "android-device", "web"
]
HOOK_ROUTES: tuple[str, ...] = get_args(HookRoute)

# A name `when.routes` keeps accepting for the routes that replaced it: `android` was one route until
# BE-0518 split it, so a condition written before the split still means every Android device.
HOOK_ROUTE_ALIASES: dict[str, tuple[HookRoute, ...]] = {
    "android": ("android-emulator", "android-device"),
}


def route_names(routes: Iterable[str]) -> set[str]:
    """The route names *routes* stands for, with each alias spelled out as the routes it covers."""
    return {r for name in routes for r in HOOK_ROUTE_ALIASES.get(name, (name,))}


# The routes each platform can run on, which the load check on a target's own condition reads to
# refuse a `routes` that names none of its owner's. `fake` has no route at all.
PLATFORM_ROUTES: dict[HookPlatform, tuple[HookRoute, ...]] = {
    "ios": ("ios-simulator", "ios-device", "ios-live"),
    "android": ("android-emulator", "android-device"),
    "web": ("web",),
    "fake": (),
}

_EMPTY_WHEN = (
    "a `when` needs at least one non-empty field among `tags`, `exclude`, `targets`, `platforms`, "
    "`routes`, and `env`; leave `when` out for an entry that always runs"
)


@dataclass(frozen=True)
class JudgedTarget:
    """One target a `when` may judge: its name, its declared platform, and its route (BE-0513).

    `route` is None for a backend with no route (`fake`), and while the run has not yet worked the
    route out — the load-time stage, which never decides a condition that names `routes`.
    """

    name: str
    platform: str
    route: str | None = None


@dataclass(frozen=True)
class WhenFacts:
    """What a `when` is decided against (BE-0497, BE-0513).

    Args:
        tags: One tag collection per scenario the condition reads: the scenario's own for a
            scenario-level entry, the selected scenarios that drive the owner for a run-level one.
            Empty, a condition naming `tags` or `exclude` never holds.
        targets: The targets `targets`, `platforms`, and `routes` judge, any one of which may
            satisfy them: the owner alone for a target's entry, every driven target for a
            scenario's own.
        env: The environment of the `bajutsu run` process.
    """

    tags: Sequence[Sequence[str]] = ()
    targets: Sequence[JudgedTarget] = ()
    env: Mapping[str, str] = field(default_factory=dict)


class HookWhen(_Model):
    """`when`: where and for which scenarios a hook entry or block runs (BE-0497, BE-0513).

    Values inside one list combine with "or", different fields with "and". `tags` and `exclude` use
    the predicate `--tag` and `--exclude` apply to selection; `targets`, `platforms`, and `routes`
    describe one and the same judged target; `env` reads the process environment, where a null asks
    for an unset variable. Every field is a plain comparison of strings, never a probe or a model.
    """

    tags: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    platforms: list[HookPlatform] = Field(default_factory=list)
    routes: list[HookRoute | Literal["android"]] = Field(default_factory=list)
    env: dict[str, str | None] = Field(default_factory=dict)
    # The `when` of the `hooks` component application that added this condition's entry (BE-0512),
    # which must hold too. Written by the expansion, never by an author: two `tags` lists can only
    # be read as "both", and one condition has no way to spell that.
    also: Annotated[list[HookWhen], SkipJsonSchema()] = Field(default_factory=list, alias="_also")

    @field_validator("env", mode="before")
    @classmethod
    def _env_strings(cls, value: Any) -> Any:
        # A YAML `true` or `1` would otherwise compare as "True" or never match at all; the author
        # meant a string, so the fix is a pair of quotes.
        if isinstance(value, dict):
            for name, wanted in value.items():
                if wanted is not None and not isinstance(wanted, str):
                    raise ValueError(
                        f"`when.env.{name}` must be a quoted string or null, got "
                        f"{type(wanted).__name__} {wanted!r}; quote the value so YAML keeps it a string"
                    )
        return value

    @model_validator(mode="after")
    def _constrains_something(self) -> Self:
        if not (
            self.tags or self.exclude or self.targets or self.platforms or self.routes or self.env
        ):
            raise ValueError(_EMPTY_WHEN)
        return self

    @property
    def parts(self) -> tuple[HookWhen, ...]:
        """This condition and every one it must hold with, each read on its own fields alone."""
        return (self, *(p for a in self.also for p in a.parts))

    @property
    def reads_tags(self) -> bool:
        """Whether the condition reads the scenario's tags."""
        return any(p.tags or p.exclude for p in self.parts)

    @property
    def reads_target(self) -> bool:
        """Whether the condition reads the judged target."""
        return any(p.targets or p.platforms or p.routes for p in self.parts)

    @property
    def needs_run(self) -> bool:
        """Whether only the run can decide it: `env` and `routes` depend on the machine and flags."""
        return any(p.env or p.routes for p in self.parts)

    def without_env(self) -> HookWhen | None:
        """This condition with every `env` field dropped, or None when nothing else is left.

        For a stage that cannot see the environment the run will have, which keeps what the rest
        of the condition decides.
        """
        # `model_copy` skips validation, so an emptied part cannot trip the empty-`when` check.
        kept = [p.model_copy(update={"env": {}, "also": []}) for p in self.parts]
        kept = [p for p in kept if p.reads_tags or p.reads_target]
        if not kept:
            return None
        return kept[0].model_copy(update={"also": kept[1:]})

    def holds(self, facts: WhenFacts) -> bool:
        """Whether this condition, and each one it must hold with, holds for *facts*.

        The tag halves of every part must hold for one and the same scenario, and the target
        halves for one and the same judged target: a run-level entry reads many scenarios and a
        scenario's own entry many targets, and "both" would otherwise be met by one per part.
        """
        parts = self.parts
        tagged = [p for p in parts if p.tags or p.exclude]
        if tagged and not any(
            all(tags_match(tags, p.tags, p.exclude) for p in tagged) for tags in facts.tags
        ):
            return False
        # Likewise the target halves, within one judged target: they describe the same one.
        judging = [p for p in parts if p.targets or p.platforms or p.routes]
        if judging and not any(all(p.target_holds(t) for p in judging) for t in facts.targets):
            return False
        for p in parts:
            if not all(
                name not in facts.env if wanted is None else facts.env.get(name) == wanted
                for name, wanted in p.env.items()
            ):
                return False
        return True

    def target_holds(self, target: JudgedTarget) -> bool:
        """Whether *target* meets `targets`, `platforms`, and `routes` together."""
        return (
            (not self.targets or target.name in self.targets)
            and (not self.platforms or target.platform in self.platforms)
            and (not self.routes or target.route in route_names(self.routes))
        )

    def matches(self, tags: Iterable[str]) -> bool:
        """Whether a scenario carrying *tags* meets the tag half of this condition."""
        carried = tuple(tags)
        return all(
            not (p.tags or p.exclude) or tags_match(carried, p.tags, p.exclude) for p in self.parts
        )

    def with_application(self, application: HookWhen) -> HookWhen:
        """This condition, required to hold together with *application*'s (BE-0512)."""
        return self.model_copy(update={"also": [*self.also, application]})

    def __str__(self) -> str:
        """The condition as reports and errors word it, listing only the fields that constrain.

        `env` prints as written, never the value a variable actually holds, which may be a secret.
        """
        lists: tuple[tuple[str, Sequence[str]], ...] = (
            ("tags", self.tags),
            ("exclude", self.exclude),
            ("targets", self.targets),
            ("platforms", self.platforms),
            ("routes", self.routes),
        )
        parts = [f"{name}: [{', '.join(values)}]" for name, values in lists if values]
        if self.env:
            pairs = ", ".join(
                f"{name}: {'null' if wanted is None else repr(wanted)}"
                for name, wanted in self.env.items()
            )
            parts.append(f"env: {{{pairs}}}")
        return " and ".join([", ".join(parts), *(str(a) for a in self.also)])


# Separates a target from a component name in the key a target's component outputs are stored and
# substituted under (BE-0512), `<target>:<name>`; neither half can hold it.
SCOPE_SEP = ":"


def split_hook_token(token: str) -> tuple[str, str]:
    """The stored name and the output key a `hooks.<name>.<key>` token reads.

    The name may carry an alias (`<as>.<name>`) and a target scope (`<target>:<name>`); a key holds
    no dot, so the last dot always separates the two. A token with no key returns an empty one.
    """
    rest = token.removeprefix("hooks.")
    name, dot, key = rest.rpartition(".")
    return (name, key) if dot else (rest, "")


def env_name(stored: str) -> str:
    """The `<name>` half of a `BAJUTSU_HOOK__<name>__<key>` variable for a stored name.

    A target scope is dropped, since a command reads the outputs of its own target alone; an alias
    dot becomes the `__` separator, so `premium.user` reads as `premium__user`.
    """
    return stored.rpartition(SCOPE_SEP)[2].replace(".", "__")


class HookOrigin(_Model):
    """Where an entry came from when a `hooks` component added it (BE-0512).

    Written by the expansion, never by an author. It rides in the dump under `_origin`, so a
    data-driven scenario that is dumped and loaded again per row keeps it.
    """

    component: str
    alias: str | None = None
    # Whether the application also prepares, through `setup` or `beforeScenario`: its teardown then
    # reads what that preparation made, so a scenario that skips the target's preparation
    # (`preconditions.inheritSetup: false`) skips this teardown too.
    prepares: bool = False

    @model_validator(mode="after")
    def _alias_is_a_name(self) -> Self:
        # The same rule `hooks.use`'s `as` meets: an alias holding `:` or `.` would split a stored
        # name in the wrong place and drop its values.
        if self.alias is not None and (why := invalid_hook_name(self.alias)) is not None:
            raise ValueError(f"`_origin.alias` {why}")
        return self


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
    # Runs the entry only where the condition holds (BE-0497, BE-0513); absent, it always runs.
    when: HookWhen | None = None
    # Set by `hooks` component expansion (BE-0512): a component's names are scoped to its target, and
    # an alias qualifies them.
    origin: Annotated[HookOrigin | None, SkipJsonSchema()] = Field(default=None, alias="_origin")

    @model_validator(mode="after")
    def _one_command(self) -> Self:
        if (self.cmd is None) == (self.use is None):
            raise ValueError(
                "a hook entry needs exactly one of `cmd` (a command line) or `use` (a command the "
                f"worker lists in namedCommands), got {'both' if self.cmd else 'neither'}"
            )
        if "when" in self.model_fields_set and self.when is None:
            # `when:` with its body commented out would otherwise widen the entry to every scenario.
            raise ValueError(_EMPTY_WHEN)
        if self.cmd is not None and not self.cmd.strip():
            raise ValueError("a hook entry's `cmd` cannot be empty")
        if self.name is not None and (why := invalid_hook_name(self.name)) is not None:
            raise ValueError(f"hook `name` {why}")
        if self.timeout <= 0:
            raise ValueError(f"a hook entry's `timeout` must be positive, got {self.timeout}")
        return self

    @property
    def qualified_name(self) -> str | None:
        """The name a token or another command reads this entry by: `<as>.<name>` under an alias."""
        if self.name is None:
            return None
        alias = self.origin.alias if self.origin is not None else None
        return f"{alias}.{self.name}" if alias is not None else self.name

    @property
    def label(self) -> str:
        """How reports and failure messages name this entry: its `name`, else its command."""
        return self.qualified_name or (self.cmd if self.cmd is not None else f"use:{self.use}")


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
    """Fail a run-level entry whose `when` names `exclude` without `tags`.

    Such a condition skips only when *every* scenario of the run carries an excluded tag, which is
    not what an author who writes it means (BE-0497). The other fields need no scenario, so a
    condition made of them alone is fine at run level (BE-0513).
    """
    for i, entry in enumerate(entries):
        parts = entry.when.parts if entry.when is not None else ()
        # The tag parts match within one scenario, so a `tags` in any part gives `exclude` its scope.
        if any(p.exclude for p in parts) and not any(p.tags for p in parts):
            raise ValueError(
                f"{where}[{i}].when names `exclude` without `tags`: a run-level entry runs when any "
                "scenario of the run matches, so `exclude` alone would almost never skip it"
            )
