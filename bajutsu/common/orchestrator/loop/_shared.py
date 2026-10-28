"""The poll intervals and failure vocabulary the step loop is written against."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from bajutsu.common.assertions import AttributeRead, EvalContext, attribute_key
from bajutsu.common.drivers import base
from bajutsu.common.scenario import Assertion, Step

_logger = logging.getLogger(__name__)


# A recursive step runner: run these steps against this active driver, return the failure or None.
# The driver is passed explicitly so a web block can hand its inner steps a WebView driver without
# any shared mutable state (BE-0172).
_ExecSteps = Callable[[list[Step], base.Driver], str | None]


def _attributes_for(
    driver: base.Driver, block: list[Assertion]
) -> dict[tuple[str, str], AttributeRead] | None:
    """Read every `attribute` assertion's element in `block` once; None when the block has none.

    Called right after each `query()` the block is polled against (BE-0466), so the read follows the
    tree and a value an action settles a beat late is caught on a later tick. A selector that fails
    to resolve becomes the assertion's reason rather than an exception, so the poll reads again
    instead of aborting the step. A backend that cannot read raises `UnsupportedAction` through: the
    preflight should have refused it, and a quiet `else` branch or a never-firing interrupt would hide
    that it did not.
    """
    reads: dict[tuple[str, str], AttributeRead] = {}
    for a in block:
        if a.attribute is None:
            continue
        key = attribute_key(a.attribute)
        if key in reads:
            continue
        try:
            reads[key] = AttributeRead(
                value=driver.read_attribute(a.attribute.sel.as_selector(), a.attribute.name)
            )
        except base.SelectorError as e:
            reads[key] = AttributeRead(error=str(e))
    return reads or None


def _with_attributes(ctx: EvalContext, driver: base.Driver, block: list[Assertion]) -> EvalContext:
    """`ctx` carrying this tick's attribute reads for `block`, or `ctx` itself when it has none."""
    reads = _attributes_for(driver, block)
    return ctx if reads is None else replace(ctx, attributes=reads)
