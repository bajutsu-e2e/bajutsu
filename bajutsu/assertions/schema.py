"""JSON-Schema loading and validation for `responseSchema` assertions.

Loads the stored schema (confined to the schemas dir so the result can't depend on the runner's
filesystem) and validates a matching exchange's response body against it. `jsonschema` is imported
lazily (the `schema` extra), so the dependency only loads when a responseSchema assertion runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bajutsu.assertions._common import AssertionResult
from bajutsu.assertions.network import match_request, request_label
from bajutsu.evidence.network import NetworkExchange
from bajutsu.scenario import ResponseSchemaMatch


@dataclass(frozen=True)
class SchemaContext:
    """The directory a `responseSchema` assertion's schema path resolves against.

    One of config `targets.<name>.schemas`, the `--schemas` flag, or `schemas/` beside the scenario.
    """

    schemas_dir: Path


def _load_schema(schema_path: str, ctx: SchemaContext, detail: str) -> object | AssertionResult:
    """Load and parse the stored JSON Schema, or an `AssertionResult` carrying why it couldn't be.

    Confines the path to the schemas dir: an absolute path or `..` traversal would read files
    outside it and make the result depend on the runner's filesystem — reject it.
    """
    schemas_dir = ctx.schemas_dir.resolve()
    schema_file = (schemas_dir / schema_path).resolve()
    if not schema_file.is_relative_to(schemas_dir):
        return AssertionResult(
            False, "responseSchema", detail, f"schema path escapes the schemas dir: {schema_path}"
        )
    if not schema_file.is_file():
        return AssertionResult(False, "responseSchema", detail, f"schema not found: {schema_path}")
    try:
        parsed: object = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return AssertionResult(False, "responseSchema", detail, f"could not read schema: {e}")
    return parsed


def _validate_instance(instance: object, schema: object, detail: str) -> AssertionResult:
    """Validate a parsed instance against a parsed schema.

    `jsonschema` is imported lazily (the `schema` extra), so the dependency only loads when a
    responseSchema assertion is evaluated.
    """
    try:
        import jsonschema
    except ImportError:
        return AssertionResult(
            False, "responseSchema", detail, "responseSchema needs the 'schema' extra (jsonschema)"
        )
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as e:
        return AssertionResult(
            False, "responseSchema", detail, f"schema validation failed: {e.message}"
        )
    except jsonschema.SchemaError as e:
        return AssertionResult(False, "responseSchema", detail, f"invalid schema: {e.message}")
    except Exception as e:
        # A bad schema (e.g. an unresolvable $ref) must fail the assertion loudly with the reason,
        # never crash the deterministic run — so any other validator error is caught here too.
        return AssertionResult(False, "responseSchema", detail, f"schema error: {e}")
    return AssertionResult(True, "responseSchema", detail)


def _eval_response_schema(
    exchanges: list[NetworkExchange], m: ResponseSchemaMatch, ctx: SchemaContext | None
) -> AssertionResult:
    """Validate the first matching exchange's response body against a stored JSON Schema (BE-0048).

    Pure over the captured exchanges + the schema file; the schema I/O and the validation are split
    into `_load_schema` and `_validate_instance`.
    """
    detail = f"responseSchema {request_label(m.request)} ~ {m.schema_path}"
    if ctx is None:
        return AssertionResult(False, "responseSchema", detail, "no schema context provided")
    ex = next((e for e in exchanges if match_request(e, m.request)), None)
    if ex is None:
        return AssertionResult(
            False, "responseSchema", detail, f"no matching exchange (observed {len(exchanges)})"
        )
    schema = _load_schema(m.schema_path, ctx, detail)
    if isinstance(schema, AssertionResult):
        return schema
    if ex.response_body is None:
        return AssertionResult(False, "responseSchema", detail, "response has no body")
    try:
        instance = json.loads(ex.response_body)
    except ValueError:
        return AssertionResult(False, "responseSchema", detail, "response body is not JSON")
    return _validate_instance(instance, schema, detail)
