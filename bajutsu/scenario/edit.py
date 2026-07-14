"""Structural edits to a scenario file for the serve Author editor (BE-0261).

The editor's two write paths — Edit's Apply (write a picked selector into a step) and Enrich's
Accept (insert proposed assertions and a settle wait) — used to edit the YAML as flat text in the
browser: split on newlines, prefix-match a step's line, splice replacement lines back in. That
re-derives structure from string shape and mis-edits any non-canonical file (flow-style steps,
comments between steps, a `:`/`#` selector value, two scenarios in one file).

This module replaces that with a parse → mutate → serialize round-trip over the scenario model and
the canonical serializer. To keep the author's comments and formatting, it re-serializes only the
changed step / expect block and splices it at a parser-identified line span (BE-0261's
scoped-block boundary — a full-file re-serialize would drop every comment). The span comes from
`yaml.compose`'s source marks, never from matching line prefixes.
"""

from __future__ import annotations

import yaml

from bajutsu import _yaml
from bajutsu.scenario.load import load_scenario_file
from bajutsu.scenario.models import STEP_ACTIONS, Assertion, Scenario, Selector, Step
from bajutsu.scenario.serialize import dump_block

# Where the resolved selector sits within each selector-bearing action's mapping: `None` means the
# action field value *is* the selector (`tap`, `doubleTap`); a string names the sub-key that holds
# it (`type.into`, `longPress.sel`, …). Actions absent here have no single selector slot Apply can
# target, so Apply refuses them rather than guessing (fail loudly — determinism first).
_SELECTOR_SLOT: dict[str, str | None] = {
    "tap": None,
    "doubleTap": None,
    "longPress": "sel",
    "type": "into",
    "selectOption": "sel",
}


class EditError(ValueError):
    """A scoped edit could not be applied (scenario/step not found, or unsupported action)."""


def apply_selector(
    text: str,
    scenario_name: str,
    step_index: int,
    selector: dict[str, object],
) -> str:
    """Write *selector* into the *step_index*-th step of *scenario_name*, returning the new YAML.

    Parses *text*, sets the selector on the located step's action through the model (so the
    serializer owns quoting of `:`/`#`-bearing values), then splices the re-serialized step back at
    its source span so surrounding comments survive. The action is read from the parsed step, so a
    stale caller-supplied action can't misdirect the edit.

    Raises:
        EditError: The scenario or step index is not found, or the step's action has no selector
            slot (e.g. `wait`, `back`) — Apply refuses rather than corrupt it.
        ValueError: *text* is not a valid scenario file, or the selector is malformed.
    """
    scenarios = load_scenario_file(text).scenarios
    scenario = _match_scenario(scenarios, scenario_name)
    if scenario is None:
        raise EditError(f"scenario '{scenario_name}' not found")
    if not 0 <= step_index < len(scenario.steps):
        raise EditError(f"step index {step_index} out of range for scenario '{scenario.name}'")

    step = scenario.steps[step_index]
    alias = _action_alias(step)
    if alias not in _SELECTOR_SLOT:
        raise EditError(f"cannot apply a selector to a '{alias}' step")

    sel = Selector.model_validate(selector).model_dump(by_alias=True, exclude_none=True)
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    slot = _SELECTOR_SLOT[alias]
    if slot is None:
        dumped[alias] = sel
    else:
        holder = dumped[alias] if isinstance(dumped.get(alias), dict) else {}
        holder[slot] = sel
        dumped[alias] = holder
    new_step = Step.model_validate(dumped)

    root = yaml.compose(text, Loader=_yaml._Loader)
    step_node = _steps_node(root, scenario.name).value[step_index]
    lines = text.split("\n")
    start, end = _content_span(step_node)
    replacement = _reindent(dump_block([new_step]), _indent_of(lines[start]))
    return _spliced(lines, [(start, end, replacement)])


def apply_enrichment(
    text: str,
    scenario_name: str,
    expect: list[dict[str, object]],
    settle: dict[str, object] | None,
) -> str:
    """Insert Enrich's proposed assertions and settle wait into *scenario_name*, returning new YAML.

    The settle wait (a `wait` step) is appended to the scenario's steps; the proposed assertions
    replace its `expect` block (creating one after the last step if absent) — the same mutation the
    old string-splicing did, now through the model and the serializer. Both edits are spliced at
    parser-identified spans so unrelated lines and comments survive.

    Raises:
        EditError: The scenario is not found.
        ValueError: *text* is not a valid scenario file, or an assertion / settle payload is
            malformed.
    """
    load_scenario_file(text)  # reject an invalid file before mutating it
    assertions = [Assertion.model_validate(a) for a in expect]
    settle_step = Step.model_validate(settle) if settle else None

    root = yaml.compose(text, Loader=_yaml._Loader)
    scenario_node = _find_scenario_node(root, scenario_name)
    steps_node = _mapping_get(scenario_node, "steps")
    lines = text.split("\n")
    edits: list[tuple[int, int, list[str]]] = []
    item_indent = _seq_item_indent(steps_node, lines, _child_indent(scenario_node, lines))

    # New content with no existing home (the settle wait; a new expect block when the scenario has
    # none) is appended after the last step as one ordered block, so the wait lands in `steps:` and
    # the fresh `expect:` follows it — never interleaved by same-line splices.
    tail: list[str] = []
    if settle_step is not None:
        tail += _reindent(dump_block([settle_step]), item_indent)

    if assertions:
        block = _reindent(dump_block(assertions), item_indent)
        expect_node = _mapping_get(scenario_node, "expect")
        if isinstance(expect_node, yaml.SequenceNode) and expect_node.value:
            first_start, _ = _content_span(expect_node.value[0])
            _, last_end = _content_span(expect_node.value[-1])
            edits.append((first_start, last_end, block))
        else:
            key_indent = _indent_of(lines[_mapping_key_line(scenario_node, "steps")])
            tail += [key_indent + "expect:", *block]

    if tail:
        after_last = (
            _content_span(steps_node.value[-1])[1]
            if isinstance(steps_node, yaml.SequenceNode) and steps_node.value
            else _mapping_key_line(scenario_node, "steps") + 1
        )
        edits.append((after_last, after_last, tail))

    return _spliced(lines, edits)


# --- scenario / node lookup -------------------------------------------------------------------


def _match_scenario(scenarios: list[Scenario], name: str) -> Scenario | None:
    """The first scenario named *name* (or the first scenario when *name* is empty)."""
    if not name:
        return scenarios[0] if scenarios else None
    return next((s for s in scenarios if s.name == name), None)


def _action_alias(step: Step) -> str:
    """The on-disk alias of the step's action (`tap`, `type`, …)."""
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    for field in STEP_ACTIONS:
        alias = Step.model_fields[field].alias or field
        if alias in dumped:
            return alias
    return "unknown"


def _scenario_nodes(root: yaml.Node) -> list[yaml.Node]:
    """The MappingNode of each scenario, for the bare-list or `{scenarios: […]}` file form."""
    if isinstance(root, yaml.SequenceNode):
        return list(root.value)
    if isinstance(root, yaml.MappingNode):
        node = _mapping_get(root, "scenarios")
        if isinstance(node, yaml.SequenceNode):
            return list(node.value)
    return []


def _find_scenario_node(root: yaml.Node, name: str) -> yaml.MappingNode:
    """The scenario MappingNode whose `name` matches — mirrors `_match_scenario`'s first-match."""
    nodes = [n for n in _scenario_nodes(root) if isinstance(n, yaml.MappingNode)]
    if not name:
        match = nodes[0] if nodes else None
    else:
        match = next((n for n in nodes if _scalar_value(_mapping_get(n, "name")) == name), None)
    if match is None:
        raise EditError(f"scenario '{name}' not found")
    return match


def _steps_node(root: yaml.Node, name: str) -> yaml.SequenceNode:
    node = _mapping_get(_find_scenario_node(root, name), "steps")
    if not isinstance(node, yaml.SequenceNode):
        raise EditError(f"scenario '{name}' has no steps sequence")
    return node


def _mapping_get(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    return next((v for k, v in node.value if _scalar_value(k) == key), None)


def _mapping_key_line(node: yaml.MappingNode, key: str) -> int:
    return int(next(k.start_mark.line for k, _ in node.value if _scalar_value(k) == key))


def _scalar_value(node: yaml.Node | None) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


# --- source spans + splicing ------------------------------------------------------------------


def _leaf_max_end(node: yaml.Node) -> tuple[int, int]:
    """The furthest (line, column) any scalar leaf under *node* ends at.

    A collection node's own `end_mark` overshoots to the next sibling — sweeping up trailing
    comments and blank lines — so the real content end is the max end over its scalar leaves.
    """
    if isinstance(node, yaml.ScalarNode):
        return (node.end_mark.line, node.end_mark.column)
    children = (
        list(node.value)
        if isinstance(node, yaml.SequenceNode)
        else [child for pair in node.value for child in pair]
    )
    best = (-1, -1)
    for child in children:
        best = max(best, _leaf_max_end(child))
    return best


def _content_span(node: yaml.Node) -> tuple[int, int]:
    """`(start_line, end_line_exclusive)` of *node*'s real content, excluding trailing comments."""
    line, column = _leaf_max_end(node)
    # A leaf ending at column 0 closed on the previous line (a block scalar's terminator); otherwise
    # its content is on `line` itself.
    last = line if column > 0 else line - 1
    return node.start_mark.line, last + 1


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" "))]


def _child_indent(scenario_node: yaml.MappingNode, lines: list[str]) -> str:
    """The indent of a scenario's keys (`steps:`), a stable fallback for an item indent."""
    return _indent_of(lines[_mapping_key_line(scenario_node, "steps")]) + "  "


def _seq_item_indent(seq: yaml.Node | None, lines: list[str], fallback: str) -> str:
    """The indent of a sequence's `- ` items, read from the first existing item (else *fallback*)."""
    if isinstance(seq, yaml.SequenceNode) and seq.value:
        return _indent_of(lines[seq.value[0].start_mark.line])
    return fallback


def _reindent(block: str, indent: str) -> list[str]:
    """Prefix every non-empty line of a zero-indented dumped block with *indent*."""
    return [indent + line if line else line for line in block.splitlines()]


def _spliced(lines: list[str], edits: list[tuple[int, int, list[str]]]) -> str:
    """Apply `(start, end_exclusive, replacement)` edits bottom-up so earlier spans keep their lines."""
    result = list(lines)
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        result[start:end] = replacement
    return "\n".join(result)
