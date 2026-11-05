"""Assertion dispatch and the small per-kind evaluators.

Evaluate a list of expect/assert against query() results (list[Element]). The list is AND-ed; one
failure fails the step. No AI is involved (machine checks only). Evaluation is total (returns
results instead of raising) so it can be placed straight into the report (manifest). The heavier
per-kind subsystems live in sibling modules: network matching in `network`, image preprocessing in
`visual`, JSON-Schema I/O in `schema`.
"""

from ._functions import _EVALUATORS as _EVALUATORS
from ._functions import _count_op as _count_op
from ._functions import _count_op_label as _count_op_label
from ._functions import _count_satisfied as _count_satisfied
from ._functions import _do_attribute as _do_attribute
from ._functions import _do_clipboard as _do_clipboard
from ._functions import _do_count as _do_count
from ._functions import _do_disabled as _do_disabled
from ._functions import _do_enabled as _do_enabled
from ._functions import _do_event as _do_event
from ._functions import _do_exists as _do_exists
from ._functions import _do_golden as _do_golden
from ._functions import _do_label as _do_label
from ._functions import _do_request as _do_request
from ._functions import _do_request_sequence as _do_request_sequence
from ._functions import _do_response_schema as _do_response_schema
from ._functions import _do_selected as _do_selected
from ._functions import _do_value as _do_value
from ._functions import _do_visual as _do_visual
from ._functions import _eval_attribute as _eval_attribute
from ._functions import _eval_clipboard as _eval_clipboard
from ._functions import _eval_count as _eval_count
from ._functions import _eval_event as _eval_event
from ._functions import _eval_exists as _eval_exists
from ._functions import _eval_golden as _eval_golden
from ._functions import _eval_request as _eval_request
from ._functions import _eval_request_sequence as _eval_request_sequence
from ._functions import _eval_state as _eval_state
from ._functions import _eval_text as _eval_text
from ._functions import _Evaluator as _Evaluator
from ._functions import _evaluator as _evaluator
from ._functions import _event_label as _event_label
from ._functions import _text_cmp as _text_cmp
from ._functions import _text_op as _text_op
from ._functions import evaluate, evaluate_one, passed
from .eval_context import AttributeRead, EvalContext, attribute_key
from .golden_context import GoldenContext

__all__ = [
    "AttributeRead",
    "EvalContext",
    "GoldenContext",
    "attribute_key",
    "evaluate",
    "evaluate_one",
    "passed",
]
