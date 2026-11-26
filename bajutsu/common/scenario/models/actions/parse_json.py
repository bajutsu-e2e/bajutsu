"""The `parseJson` action: copy values out of JSON text into vars.*, by path (BE-0516)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model


class ParseJsonField(_Model):
    """One `parseJson.extract` entry: the value at `path`, stored as `vars.<var>`."""

    var: str
    path: str


def first_duplicate_var(names: Iterable[str]) -> str | None:
    """The first name in *names* that repeats, or None.

    Shared by the load-time check below (each entry's as-written `var`) and the runner's handler
    (the substituted names): a collision a `${...}` token hides shows only after substitution.
    """
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


class ParseJson(_Model):
    """`parseJson` — read JSON text and copy the values at the given paths into `${vars.*}`.

    `from` is a string with the usual `${...}` interpolation. Each `path` is a fixed grammar of
    object keys and zero-based `[n]` indexes, not a query language. A string value is stored as it
    stands, any other as compact JSON text. All entries resolve together or none is written. Local
    and deterministic — no model, no network.
    """

    from_: str = Field(alias="from")
    extract: list[ParseJsonField] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_vars(self) -> Self:
        dup = first_duplicate_var(field.var for field in self.extract)
        if dup is not None:
            raise ValueError(f"parseJson: var {dup!r} is used by more than one extract entry")
        return self
