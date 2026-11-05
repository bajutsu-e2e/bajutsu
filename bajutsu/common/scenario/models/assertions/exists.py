"""The `exists` assertion: an element is present, or deliberately absent."""

from __future__ import annotations

from typing import Any

from pydantic import GetJsonSchemaHandler, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.selector import Selector


class Exists(_Model):
    """`exists: { <selector>, negate? }` (selector inline, optional negate)."""

    sel: Selector
    negate: bool = False

    @model_validator(mode="before")
    @classmethod
    def _inline(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sel" not in data:
            d = dict(data)
            negate = d.pop("negate", False)
            return {"sel": d, "negate": negate}
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # `sel` is how `_inline` stores the selector, not how a file writes it: the documented form
        # puts the selector's fields inline beside `negate`, so the schema states that form first.
        nested = handler(core_schema)
        selector = handler.resolve_ref_schema(handler(Selector.__pydantic_core_schema__))
        inline: JsonSchemaValue = {
            "type": "object",
            "properties": {
                **selector.get("properties", {}),
                "negate": {"default": False, "title": "Negate", "type": "boolean"},
            },
            "additionalProperties": False,
        }
        return {"title": "Exists", "description": cls.__doc__, "anyOf": [inline, nested]}
