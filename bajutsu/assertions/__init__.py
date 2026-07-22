"""Assertion evaluation.

Evaluate a list of expect/assert against query() results (list[Element]). The list is AND-ed; one
failure fails the step. No AI is involved (machine checks only). Evaluation is total (returns
results instead of raising) so it can be placed straight into the report (manifest).

The package splits along the five concerns that only shared a home because they all end in an
`AssertionResult`: the dispatcher and small UI evaluators (`evaluate`), network-timeline matching
also used by the web mock router and `until: {request}` waits (`network`), the visual
image-preprocessing subsystem (`visual`), and JSON-Schema loading/validation (`schema`); the shared
`AssertionResult` and tiny helpers live in `_common`. This module re-exports the public surface, so
`from bajutsu.assertions import evaluate` and friends are unchanged (BE-0250).
"""

from __future__ import annotations

from bajutsu.assertions._common import AssertionResult, sel_str
from bajutsu.assertions.evaluate import (
    EvalContext,
    GoldenContext,
    evaluate,
    evaluate_one,
    passed,
)
from bajutsu.assertions.network import count_matching, match_request, request_label
from bajutsu.assertions.schema import SchemaContext
from bajutsu.assertions.visual import VisualContext, VisualEvidence

__all__ = [
    "AssertionResult",
    "EvalContext",
    "GoldenContext",
    "SchemaContext",
    "VisualContext",
    "VisualEvidence",
    "count_matching",
    "evaluate",
    "evaluate_one",
    "match_request",
    "passed",
    "request_label",
    "sel_str",
]
