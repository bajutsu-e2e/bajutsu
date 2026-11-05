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
    """Read every `attribute` assertion's element in `block`; None when the block has none.

    Called right after each `query()` the block is polled against (BE-0466), so the read follows the
    tree and a value an action settles a beat late is caught on a later tick. The whole block goes
    in one `read_attributes` call, so its assertions are judged against one instant: two reads a few
    milliseconds apart could pass a pair of values that never held together. A selector that fails
    to resolve comes back as that pair's reason, so the poll reads again instead of aborting the
    step. A backend that cannot read raises `UnsupportedAction` through: the preflight should have
    refused it, and a quiet `else` branch or a never-firing interrupt would hide that it did not.
    """
    wanted: dict[tuple[str, str], tuple[base.Selector, str]] = {}
    for a in block:
        if a.attribute is not None:
            key = attribute_key(a.attribute)
            wanted.setdefault(key, (a.attribute.sel.as_selector(), a.attribute.name))
    if not wanted:
        return None
    results = driver.read_attributes(list(wanted.values()))
    return {
        key: AttributeRead(error=str(r))
        if isinstance(r, base.SelectorError)
        else AttributeRead(value=r)
        for key, r in zip(wanted, results, strict=True)
    }


def _with_attributes(ctx: EvalContext, driver: base.Driver, block: list[Assertion]) -> EvalContext:
    """`ctx` carrying this tick's attribute reads for `block`, or `ctx` itself when it has none."""
    reads = _attributes_for(driver, block)
    return ctx if reads is None else replace(ctx, attributes=reads)
