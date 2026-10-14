"""Fields every platform's `app` group shares, and the field validators the platform models reuse."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, WithJsonSchema, model_validator

from bajutsu.common.devices.version import VersionSpec
from bajutsu.common.scenario import Exists

from .._model import _Model


class StartWhen(_Model):
    """`app.startWhen`: the condition a run waits for before the first step (BE-0452).

    The same form an `interrupts` entry uses for its `condition`, so the config has one way to say
    "this element is on screen". Only `exists` is accepted, and without `negate`, because that is
    what the readiness gate evaluates today; any other assertion kind fails as an unknown field.
    """

    exists: Exists

    @model_validator(mode="after")
    def _no_negate(self) -> Self:
        if self.exists.negate:
            raise ValueError("app.startWhen.exists cannot set negate: the run waits for presence")
        return self


class AppCommon(_Model):
    """The `app` fields every platform has: readiness and the identifier contract."""

    start_when: StartWhen | None = Field(default=None, alias="startWhen")
    id_namespaces: list[str] = Field(default_factory=list, alias="idNamespaces")


class LaunchEnv(_Model):
    """`app.launch` on a platform that reads only environment variables (the web)."""

    env: dict[str, str] = Field(default_factory=dict)


# How the app is (re)installed before each run when the target gives an `app.path` (BE-0452):
# `clean` uninstalls first, `overwrite` installs over the existing app and keeps its data. Unset
# leaves it to the scenario, then the built-in `clean`.
Reinstall = Literal["clean", "overwrite"]


def coerce_range(value: Any) -> Any:
    """Read an unquoted YAML integer as a range, and refuse an unquoted decimal.

    `apiLevel: 34` is a natural way to write one major version, so an integer becomes its text. A
    YAML float has already lost what the author wrote (`18.10` reads as `18.1`), so it fails and asks
    for quotes rather than silently selecting another version.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise ValueError(  # noqa: TRY004 - Pydantic turns only ValueError into a field error
            f"version range {value!r} is a YAML number; quote it so its digits survive, "
            'for example os: "18.10"'
        )
    return value


def check_range(value: str | None) -> str | None:
    """Reject a malformed version range at load, keeping the text as written.

    The text stays the stored value, so a printed config and the generated JSON Schema show what the
    author wrote; the run resolution parses it again where it compares.
    """
    if value is not None:
        VersionSpec.parse(value)
    return value


# A version-range field with both validators attached, so a platform's range field cannot acquire one
# without the other. Its JSON Schema admits the integer `coerce_range` reads, as `apiLevel: 34`.
RangeField = Annotated[
    str | None,
    BeforeValidator(coerce_range),
    AfterValidator(check_range),
    WithJsonSchema({"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}),
]


def check_list(field: str, value: str | list[str] | None) -> str | list[str] | None:
    """Reject an empty or repeating list on a field whose list multiplies runs.

    An empty list would make no run at all, and a repeated value would run the same coordinates
    twice; both read as a mistake rather than as a request.
    """
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{field} lists no value")
        if len(set(value)) != len(value):
            raise ValueError(f"{field} lists a value twice")
    return value
