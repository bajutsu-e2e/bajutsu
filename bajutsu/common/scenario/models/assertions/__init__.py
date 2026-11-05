"""Machine-checkable assertions and the wait conditions that reuse them.

Covers the network-traffic matcher, existence/text/count/state checks, visual regression,
and the `Assertion` aggregator that selects exactly one kind.
"""

from ._endpoint_match import _EndpointMatch as _EndpointMatch
from .assertion import _ASSERTION_KINDS as _ASSERTION_KINDS
from .assertion import Assertion
from .attribute_match import AttributeMatch
from .clipboard_match import ClipboardMatch
from .count_match import CountMatch
from .count_op import CountOp
from .event_match import EventMatch
from .exclude_region import ExcludeRegion
from .exists import Exists
from .golden_match import GoldenMatch
from .gone import Gone
from .request_match import RequestMatch
from .response_schema_match import ResponseSchemaMatch
from .selector_region import SelectorRegion
from .text_match import TextMatch
from .visual_match import VisualMatch
from .wait import Wait
from .wait_request import WaitRequest

__all__ = [
    "Assertion",
    "AttributeMatch",
    "ClipboardMatch",
    "CountMatch",
    "CountOp",
    "EventMatch",
    "ExcludeRegion",
    "Exists",
    "GoldenMatch",
    "Gone",
    "RequestMatch",
    "ResponseSchemaMatch",
    "SelectorRegion",
    "TextMatch",
    "VisualMatch",
    "Wait",
    "WaitRequest",
]
