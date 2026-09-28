"""Static e2e coverage map for a scenario suite — which declared id namespaces it touches.

The read-only counterpart to doctor's per-screen convention score: doctor grades the ids an app
*exposes* on one screen, this grades the ids a *suite* exercises. It walks every scenario without a
device (reusing audit's selector walk), groups the stable ids it references by namespace, and
measures them against the app's declared `idNamespaces` — reporting per-namespace coverage, the
gap list (declared namespaces no scenario touches), and off-namespace ids (referenced ids whose
namespace was never declared). No model is consulted, no scenario is run, and no verdict is
touched: a coverage report is advisory, never a CI gate.
"""

from ._functions import _TEMPLATE_DIR as _TEMPLATE_DIR
from ._functions import _assertion_requests as _assertion_requests
from ._functions import _endpoint as _endpoint
from ._functions import _env as _env
from ._functions import _evidence_files as _evidence_files
from ._functions import _is_literal_id as _is_literal_id
from ._functions import (
    coverage,
    endpoint_coverage,
    observed_id_coverage,
    read_element_lists,
    read_exchanges,
    read_observed_ids,
    referenced_requests,
    render,
    render_endpoints,
    render_html,
    render_observed_ids,
    render_screens,
    screen_coverage,
    screen_fingerprints,
    screen_refs,
    step_requests,
)
from .coverage import Coverage
from .endpoint_coverage import EndpointCoverage
from .namespace_coverage import NamespaceCoverage
from .observed_id_coverage import ObservedIdCoverage
from .screen_coverage import ScreenCoverage
from .screen_ref import ScreenRef

__all__ = [
    "Coverage",
    "EndpointCoverage",
    "NamespaceCoverage",
    "ObservedIdCoverage",
    "ScreenCoverage",
    "ScreenRef",
    "coverage",
    "endpoint_coverage",
    "observed_id_coverage",
    "read_element_lists",
    "read_exchanges",
    "read_observed_ids",
    "referenced_requests",
    "render",
    "render_endpoints",
    "render_html",
    "render_observed_ids",
    "render_screens",
    "screen_coverage",
    "screen_fingerprints",
    "screen_refs",
    "step_requests",
]
