"""The `eval` action: transform a value through a fixed operation vocabulary into vars.* (BE-0472)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _exactly_one, _Model

from .eval_ops import EvalOpError, check_ops


class EvalTarget(_Model):
    """`into` of an `eval` step: one `${vars.*}` slot, or one slot per list element.

    `vars` spreads a list result over several names in order — the first element into the first
    name, and so on. A list longer than `vars` leaves its extra elements unassigned; a shorter one
    fails the step at run time, since a name left unset would surface later as an unrelated mismatch.
    """

    var: str | None = None
    vars: list[str] | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        _exactly_one(self, ("var", "vars"), "eval into")
        if self.vars is not None:
            if not self.vars:
                raise ValueError("into.vars must name at least one variable")
            if len(set(self.vars)) != len(self.vars):
                raise ValueError("into.vars must not name the same variable twice")
        return self

    @property
    def names(self) -> list[str]:
        """Every variable this target writes, in order."""
        return [self.var] if self.var is not None else list(self.vars or [])


class Eval(_Model):
    """`eval` — run a list of operations over a string and store the result in `${vars.*}`.

    Despite the name it runs no code: `ops` draws from a fixed vocabulary (`eval_ops.SIGNATURES`)
    a scenario cannot extend, and the loader checks every argument and the string/list type flow
    before the run starts. A list result is stored as compact JSON array text. Local and
    deterministic — no model, no network.
    """

    from_: str = Field(alias="from")
    # Raw rather than one model per operation: an argument may hold a `${...}` token whose value only
    # the run knows, so the parsed form is rebuilt from this list after substitution (`parse_ops`).
    ops: list[str | dict[str, Any]]
    into: EvalTarget

    @model_validator(mode="after")
    def _ops_type_check(self) -> Self:
        try:
            kind = check_ops(self.ops)
        except EvalOpError as e:
            raise ValueError(f"eval: {e}") from None
        if self.into.vars is not None and kind != "list":
            raise ValueError("eval: into.vars needs a list result, but the ops end with a string")
        return self
