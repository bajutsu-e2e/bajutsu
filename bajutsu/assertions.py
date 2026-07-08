"""Assertion evaluation.

Evaluate a list of expect/assert against query() results (list[Element]). The
list is AND-ed; one failure fails the step. No AI is involved (machine checks
only).

Evaluation is total (returns results instead of raising) so it can be placed
straight into the report (manifest).
"""

from __future__ import annotations

import functools
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.network import NetworkExchange
from bajutsu.scenario import (
    Assertion,
    ClipboardMatch,
    CountMatch,
    CountOp,
    EventMatch,
    ExcludeRegion,
    Exists,
    GoldenMatch,
    RequestMatch,
    ResponseSchemaMatch,
    Selector,
    SelectorRegion,
    TextMatch,
    VisualMatch,
)


@functools.lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Cached re.compile — avoids recompiling the same pattern on every poll iteration."""
    return re.compile(pattern)


@dataclass(frozen=True)
class VisualEvidence:
    """Image evidence for a visual assertion, carried into the manifest/report.

    Paths are *run-dir-relative* (the same scheme as artifacts), so the self-contained
    report and the serve UI can reference them. `baseline_name` is the YAML key into the
    baselines dir — what `approve` promotes the actual screenshot to.
    """

    baseline_name: str
    actual: str  # the captured screenshot
    baseline: str | None = None  # the baseline copy in the run dir (None if missing)
    diff: str | None = None  # the diff visualization (None when identical / missing)
    diff_pct: float | None = None
    missing: bool = False  # baseline did not exist yet (first run)
    engine: str | None = None  # the compare engine used (exact / pixelmatch; BE-0165)
    # Provenance for element-scoped comparison / selector masking (BE-0171).
    element_scoped: bool = False  # the comparison was cropped to one element's frame
    # selectors that resolved to a mask, in order (a list so it round-trips through the manifest)
    masked_selectors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssertionResult:
    """The outcome of one assertion check, carried into the manifest/report."""

    ok: bool
    kind: str
    detail: str  # what was checked (for the report)
    reason: str = ""  # failure reason (empty when ok)
    visual: VisualEvidence | None = None  # set only for `visual` assertions


@dataclass(frozen=True)
class VisualContext:
    """Paths a visual assertion needs.

    The current screenshot, the baselines directory, where to write diff images, and the run dir
    root (so image paths can be expressed run-dir-relative for the report).
    """

    screenshot_path: Path
    baselines_dir: Path
    diff_dir: Path
    run_dir: Path
    default_compare: str = "exact"


@dataclass(frozen=True)
class SchemaContext:
    """The directory a `responseSchema` assertion's schema path resolves against.

    One of config `apps.<name>.schemas`, the `--schemas` flag, or `schemas/` beside the scenario.
    """

    schemas_dir: Path


@dataclass(frozen=True)
class GoldenContext:
    """Paths a `golden` assertion needs (BE-0006).

    The golden JSON path (from the assertion's `path` field) is resolved against `goldens_dir`.
    `screen`, when given, is the authoritative device screen bounds for frame sanity checks;
    when absent, the bounds are derived from the live elements — a weaker fallback since
    elements at the screen edge make the check tautological for overflow detection.
    """

    goldens_dir: Path
    screen: base.Frame | None = None


def _sel_str(sel: Selector) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in sel.as_selector().items())


def _resolve_one(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """Resolve a single element. On failure returns (None, reason).

    Ambiguous / not-found are treated as assertion failures.
    """
    try:
        return base.resolve_unique(elements, sel.as_selector()), ""
    except base.SelectorError as e:
        return None, str(e)


def _eval_exists(elements: list[base.Element], a: Exists) -> AssertionResult:
    found = len(base.find_all(elements, a.sel.as_selector())) >= 1
    ok = found != a.negate
    want = "absent" if a.negate else "present"
    reason = "" if ok else f"expected {want} but was {'present' if found else 'absent'}"
    return AssertionResult(ok, "exists", f"{want}: {_sel_str(a.sel)}", reason)


def _eval_text(elements: list[base.Element], kind: str, a: TextMatch) -> AssertionResult:
    el, err = _resolve_one(elements, a.sel)
    detail_base = f"{kind}: {_sel_str(a.sel)}"
    if el is None:
        return AssertionResult(False, kind, detail_base, err)
    actual = el["value"] if kind == "value" else el["label"]
    op, expected = _text_op(a)
    ok = _text_cmp(actual, op, expected)
    reason = "" if ok else f"expected {op}={expected!r} but actual={actual!r}"
    return AssertionResult(ok, kind, f"{kind} {op}={expected!r}: {_sel_str(a.sel)}", reason)


def _text_op(a: TextMatch) -> tuple[str, str]:
    if a.equals is not None:
        return "equals", a.equals
    if a.contains is not None:
        return "contains", a.contains
    assert a.matches is not None  # exactly one is guaranteed by scenario validation
    return "matches", a.matches


def _text_cmp(actual: str | None, op: str, expected: str) -> bool:
    if actual is None:
        return False
    if op == "equals":
        return actual == expected
    if op == "contains":
        return expected in actual
    return _compile(expected).search(actual) is not None


def _eval_count(elements: list[base.Element], a: CountMatch) -> AssertionResult:
    n = len(base.find_all(elements, a.sel.as_selector()))
    op, k = _count_op(a)
    ok = {"equals": n == k, "atLeast": n >= k, "atMost": n <= k}[op]
    reason = "" if ok else f"expected count {op}={k} but n={n}"
    return AssertionResult(ok, "count", f"count {op}={k}: {_sel_str(a.sel)}", reason)


def _count_op(a: CountMatch) -> tuple[str, int]:
    if a.equals is not None:
        return "equals", a.equals
    if a.at_least is not None:
        return "atLeast", a.at_least
    assert a.at_most is not None  # exactly one is guaranteed by scenario validation
    return "atMost", a.at_most


def _eval_state(elements: list[base.Element], kind: str, sel: Selector) -> AssertionResult:
    el, err = _resolve_one(elements, sel)
    detail = f"{kind}: {_sel_str(sel)}"
    if el is None:
        return AssertionResult(False, kind, detail, err)
    traits = el["traits"]
    if kind == "enabled":
        ok = base.Trait.NOT_ENABLED not in traits
    elif kind == "disabled":
        ok = base.Trait.NOT_ENABLED in traits
    else:  # selected
        ok = base.Trait.SELECTED in traits
    reason = "" if ok else f"not {kind}: traits={traits}"
    return AssertionResult(ok, kind, detail, reason)


def match_request(ex: NetworkExchange, req: RequestMatch) -> bool:
    """Whether one observed exchange satisfies a request matcher.

    Shared by the `request` assertion and the web mock router, so a mock stubs exactly what an
    assertion would match.

    Args:
        ex: One observed network exchange.
        req: The matcher; only its set (non-`None`) fields are checked, AND-ed together.

    Returns:
        True iff every set field of `req` matches `ex`.
    """
    # Straight-line early returns, kept allocation-free on purpose: this runs in `until: {request}`
    # polling and per-exchange matching loops, so it must stay lightweight (no per-call closures).
    if req.method is not None and ex.method.upper() != req.method.upper():
        return False
    if req.url is not None and ex.url != req.url:
        return False
    if req.url_matches is not None and _compile(req.url_matches).search(ex.url) is None:
        return False
    if req.path is not None and ex.path != req.path:
        return False
    if req.path_matches is not None and _compile(req.path_matches).search(ex.path) is None:
        return False
    if req.status is not None and ex.status != req.status:
        return False
    return not (
        req.body_matches is not None
        and (ex.request_body is None or _compile(req.body_matches).search(ex.request_body) is None)
    )


def count_matching(exchanges: list[NetworkExchange], req: RequestMatch) -> int:
    """How many observed exchanges satisfy the request matcher.

    Shared by the `request` assertion and the `until: { request }` wait.
    """
    return sum(1 for ex in exchanges if match_request(ex, req))


def request_label(req: RequestMatch, *, with_count: bool = True) -> str:
    """A compact human description of a request matcher (e.g. ``GET /items status=200``).

    Args:
        req: The request matcher to describe.
        with_count: When False, the matcher's `count` is left out of the label — used where `count`
            is not part of the check (e.g. `requestSequence`, which is about order), so the label
            doesn't imply a field that is ignored.

    Returns:
        The matcher's set fields joined into one space-separated line.
    """
    parts: list[str] = []
    if req.method is not None:
        parts.append(req.method.upper())
    if req.url is not None:
        parts.append(req.url)
    if req.url_matches is not None:
        parts.append(f"url~{req.url_matches}")
    if req.path is not None:
        parts.append(req.path)
    if req.path_matches is not None:
        parts.append(f"~{req.path_matches}")
    if req.status is not None:
        parts.append(f"status={req.status}")
    if req.body_matches is not None:
        parts.append(f"body~{req.body_matches}")
    if with_count and req.count is not None:
        parts.append(f"count={req.count}")
    return " ".join(parts)


def _eval_request(exchanges: list[NetworkExchange], req: RequestMatch) -> AssertionResult:
    n = count_matching(exchanges, req)
    ok = (n == req.count) if req.count is not None else (n >= 1)
    detail = f"request {request_label(req)}"
    if ok:
        return AssertionResult(True, "request", detail)
    reason = (
        f"expected count={req.count} but matched {n}"
        if req.count is not None
        else f"no matching exchange (observed {len(exchanges)})"
    )
    return AssertionResult(False, "request", detail, reason)


def _count_op_label(c: CountOp) -> str:
    if c.equals is not None:
        return f"=={c.equals}"
    if c.at_least is not None:
        return f">={c.at_least}"
    return f"<={c.at_most}"


def _count_satisfied(n: int, c: CountOp | None) -> bool:
    """Whether `n` matches satisfy the count operator (default: at least one)."""
    if c is None:
        return n >= 1
    if c.equals is not None:
        return n == c.equals
    if c.at_least is not None:
        return n >= c.at_least
    return c.at_most is not None and n <= c.at_most


def _json_text(value: object) -> str:
    """Canonical text form of a JSON value for comparing an event body field.

    Booleans and null render JSON-style (`true` / `false` / `null`), and a nested array / object
    renders as compact JSON, so a YAML matcher reads the way the captured body does, not as a Python
    `repr` (`True` / `None` / single-quoted dicts). Numbers / strings keep their plain form.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _event_body_matches(ex: NetworkExchange, body: dict[str, str]) -> bool:
    """Whether the exchange's JSON request body carries every given field, each equal (as text).

    A non-JSON / non-object / absent body matches no body criterion.
    """
    if not body:
        return True
    if ex.request_body is None:
        return False
    try:
        parsed = json.loads(ex.request_body)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return all(key in parsed and _json_text(parsed[key]) == want for key, want in body.items())


def _event_label(m: EventMatch) -> str:
    """Compact human description of an event matcher (endpoint + body + count)."""
    parts: list[str] = []
    if m.method is not None:
        parts.append(m.method.upper())
    if m.url is not None:
        parts.append(m.url)
    if m.url_matches is not None:
        parts.append(f"url~{m.url_matches}")
    if m.path is not None:
        parts.append(m.path)
    if m.path_matches is not None:
        parts.append(f"~{m.path_matches}")
    if m.body:
        parts.append(f"body={m.body}")
    if m.count is not None:
        parts.append(f"count{_count_op_label(m.count)}")
    return " ".join(parts)


def _eval_event(exchanges: list[NetworkExchange], m: EventMatch) -> AssertionResult:
    """Assert an analytics/telemetry event the app sent (BE-0048).

    Filter the timeline by the event's endpoint (reusing the request matcher), then by its
    structured request-body fields, and check the surviving count against the operator. Pure over
    the captured exchanges.
    """
    endpoint = (m.method, m.url, m.url_matches, m.path, m.path_matches)
    if any(v is not None for v in endpoint):
        req = RequestMatch(
            method=m.method,
            url=m.url,
            urlMatches=m.url_matches,
            path=m.path,
            pathMatches=m.path_matches,
        )
        candidates = [ex for ex in exchanges if match_request(ex, req)]
    else:
        candidates = exchanges
    n = sum(1 for ex in candidates if _event_body_matches(ex, m.body))
    detail = f"event {_event_label(m)}"
    if _count_satisfied(n, m.count):
        return AssertionResult(True, "event", detail)
    want = f"count{_count_op_label(m.count)}" if m.count is not None else "at least one"
    return AssertionResult(
        False,
        "event",
        detail,
        f"expected {want}, matched {n} (observed {len(exchanges)} exchanges)",
    )


def _eval_request_sequence(
    exchanges: list[NetworkExchange], seq: list[RequestMatch]
) -> AssertionResult:
    """Assert a set of request matchers were observed in order (BE-0048).

    Each matches a distinct exchange at a strictly later position than the previous, so unrelated
    traffic may interleave. A greedy forward scan is optimal for this order-preserving subsequence.
    Pure over the timeline.
    """
    detail = "requestSequence " + " → ".join(request_label(r, with_count=False) for r in seq)
    i = 0
    for pos, req in enumerate(seq):
        while i < len(exchanges) and not match_request(exchanges[i], req):
            i += 1
        if i >= len(exchanges):
            reason = (
                f"step {pos} ({request_label(req, with_count=False)}) not observed in order "
                f"(matched {pos} of {len(seq)} so far; observed {len(exchanges)} exchanges)"
            )
            return AssertionResult(False, "requestSequence", detail, reason)
        i += 1
    return AssertionResult(True, "requestSequence", detail)


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


def _assign_requests(exchanges: list[NetworkExchange], reqs: list[RequestMatch]) -> list[int]:
    """Assign each request matcher a *distinct* exchange — one `request` ↔ one exchange.

    Maximum bipartite matching (Kuhn's augmenting paths) so a broad matcher never steals
    the only exchange a more specific one needs. Returns, per matcher, the exchange index
    it was assigned, or -1 when none is left for it.
    """
    adj = [[j for j, ex in enumerate(exchanges) if match_request(ex, req)] for req in reqs]
    ex_to_req = [-1] * len(exchanges)
    assigned = [-1] * len(reqs)

    def augment(i: int, seen: list[bool]) -> bool:
        for j in adj[i]:
            if not seen[j]:
                seen[j] = True
                if ex_to_req[j] == -1 or augment(ex_to_req[j], seen):
                    ex_to_req[j], assigned[i] = i, j
                    return True
        return False

    for i in range(len(reqs)):
        augment(i, [False] * len(exchanges))
    return assigned


def _request_assignment_result(
    req: RequestMatch, assigned_ex: int, exchanges: list[NetworkExchange]
) -> AssertionResult:
    detail = f"request {request_label(req)}"
    if assigned_ex != -1:
        return AssertionResult(True, "request", detail)
    matched_any = any(match_request(ex, req) for ex in exchanges)
    reason = (
        "matching exchange already taken by another request (request ↔ exchange is one-to-one)"
        if matched_any
        else f"no matching exchange (observed {len(exchanges)})"
    )
    return AssertionResult(False, "request", detail, reason)


def _visual_scale(
    screenshot_path: Path, elements: list[base.Element]
) -> tuple[float, float] | None:
    """The screenshot-pixel per element-point scale, or None if it can't be derived.

    Element frames are in points; the screenshot is in device pixels (2x/3x on retina). The scale
    is the screenshot's pixel size over the point-space screen size (the element extent), so a
    resolved frame maps onto the actual image. Returns None when there are no elements to size the
    screen from — the caller then can't resolve any selector to a frame.
    """
    from bajutsu.elements import screen_size_from_elements

    sw, sh = screen_size_from_elements(elements)
    if sw <= 0 or sh <= 0:
        return None
    from PIL import Image

    with Image.open(screenshot_path) as img:
        iw, ih = img.size
    return iw / sw, ih / sh


def _frame_to_px(frame: base.Frame, scale: tuple[float, float]) -> ExcludeRegion:
    """A point-space element frame scaled to a screenshot-pixel rectangle."""
    sx, sy = scale
    x, y, w, h = frame
    return ExcludeRegion(x=round(x * sx), y=round(y * sy), w=round(w * sx), h=round(h * sy))


def _resolve_mask(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """Resolve a selector *mask*: not-found is a no-op, ambiguous fails (prime directive 2).

    Returns (element, "") when a single element matches, (None, "") when nothing matches (there is
    nothing on screen to hide), and (None, reason) when the selector is ambiguous.
    """
    try:
        return base.resolve_unique(elements, sel.as_selector()), ""
    except base.ElementNotFound:
        return None, ""
    except base.AmbiguousSelector as e:
        return None, str(e)


def _shift(region: ExcludeRegion, dx: float, dy: float) -> ExcludeRegion:
    """A mask rectangle translated into a cropped image's local coordinates."""
    return ExcludeRegion(x=region.x - dx, y=region.y - dy, w=region.w, h=region.h)


@dataclass(frozen=True)
class _Prepared:
    """The result of visual preprocessing: what to compare, plus the frame data later steps reuse.

    `compare_actual` is the image handed to the compare engine (the element crop when scoped, else
    the whole screenshot); `actual_rel` is its run-dir-relative path for the evidence. `crop` and
    `scale` are None for a whole-screen comparison and set once frames were resolved.
    """

    compare_actual: Path
    actual_rel: str
    crop: ExcludeRegion | None
    scale: tuple[float, float] | None


def _prepare_visual_comparison(
    ctx: VisualContext, a: VisualMatch, elements: list[base.Element], name: str
) -> _Prepared | AssertionResult:
    """Resolve frames and crop the actual to the scoped element, before the baseline check.

    Element scoping and selector masks (BE-0171) resolve against the live element tree in
    screenshot-pixel space; a comparison that needs neither keeps the whole-screen behavior. The
    crop happens *before* the missing-baseline check because it is both what we compare and what
    `approve` promotes — so the baseline is the element even on the first run (otherwise the first
    approve would store a whole-screen baseline and every later run would size-mismatch).

    Returns the prepared comparison, or an AssertionResult when preprocessing fails (Pillow missing,
    no elements to resolve against, or an unresolvable / empty-frame element scope).
    """
    detail = f"visual ≈ {a.baseline}"
    actual_rel = _rel(ctx.run_dir, ctx.screenshot_path)
    needs_frames = a.element is not None or any(
        isinstance(r, SelectorRegion) for r in a.exclude or []
    )
    if not needs_frames:
        return _Prepared(ctx.screenshot_path, actual_rel, crop=None, scale=None)

    try:
        from PIL import Image
    except ImportError:
        return AssertionResult(
            False, "visual", detail, "visual assertions need the 'visual' extra (Pillow)"
        )
    scale = _visual_scale(ctx.screenshot_path, elements)
    if scale is None:
        return AssertionResult(
            False, "visual", detail, "cannot resolve selectors: no elements on screen"
        )
    if a.element is None:
        return _Prepared(ctx.screenshot_path, actual_rel, crop=None, scale=scale)

    el, err = _resolve_one(elements, a.element)
    if el is None:
        ev = VisualEvidence(baseline_name=a.baseline, actual=actual_rel, element_scoped=True)
        return AssertionResult(False, "visual", detail, f"element {err}", visual=ev)
    crop = _frame_to_px(el["frame"], scale)
    if crop.w <= 0 or crop.h <= 0:
        # A zero-area frame (an off-screen / collapsed element) can't be cropped — fail
        # cleanly rather than letting Pillow raise on an empty image.
        ev = VisualEvidence(baseline_name=a.baseline, actual=actual_rel, element_scoped=True)
        return AssertionResult(
            False,
            "visual",
            detail,
            f"element has an empty frame: {_sel_str(a.element)}",
            visual=ev,
        )
    ctx.diff_dir.mkdir(parents=True, exist_ok=True)
    cropped_path = ctx.diff_dir / f"actual-{name}"
    box = (int(crop.x), int(crop.y), int(crop.x + crop.w), int(crop.y + crop.h))
    with Image.open(ctx.screenshot_path) as img:
        img.crop(box).save(cropped_path)
    return _Prepared(cropped_path, _rel(ctx.run_dir, cropped_path), crop=crop, scale=scale)


def _resolve_masks(
    a: VisualMatch,
    elements: list[base.Element],
    scale: tuple[float, float] | None,
    crop: ExcludeRegion | None,
    detail: str,
) -> tuple[list[ExcludeRegion], list[str]] | AssertionResult:
    """Resolve the compare-time exclude masks, translating them into crop-local coordinates.

    Plain rectangles pass through unchanged; selector masks resolve against the live tree to a pixel
    rectangle (an ambiguous selector fails, a match of nothing is a no-op). When element-scoped, the
    masks are shifted into the crop's local coordinate space. Returns `(masks, masked_selectors)`, or
    an AssertionResult when an exclude selector is ambiguous.
    """
    masks: list[ExcludeRegion] = []
    masked_selectors: list[str] = []
    for r in a.exclude or []:
        if not isinstance(r, SelectorRegion):
            masks.append(r)
            continue
        assert scale is not None  # a SelectorRegion sets needs_frames, so scale is resolved
        el, err = _resolve_mask(elements, r.selector)
        if err:
            return AssertionResult(False, "visual", detail, f"exclude selector {err}")
        if el is None:
            continue  # matched nothing — nothing on screen to hide
        masks.append(_frame_to_px(el["frame"], scale))
        masked_selectors.append(_sel_str(r.selector))
    if crop is not None:
        masks = [_shift(m, crop.x, crop.y) for m in masks]
    return masks, masked_selectors


def _resolve_baselines(ctx: VisualContext, baseline_path: Path, name: str) -> tuple[Path, Path]:
    """Prepare the run-dir baseline copy and the diff path for a compare.

    Copies the baseline into the run dir (so the report and serve are self-contained) and returns
    `(baseline_copy, diff_path)`. Called only once the baseline is known to exist.
    """
    ctx.diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = ctx.diff_dir / f"diff-{name}"
    baseline_copy = ctx.diff_dir / f"baseline-{name}"
    shutil.copyfile(baseline_path, baseline_copy)
    return baseline_copy, diff_path


def _eval_visual(
    ctx: VisualContext | None, a: VisualMatch, elements: list[base.Element]
) -> AssertionResult:
    detail = f"visual ≈ {a.baseline}"
    if ctx is None:
        return AssertionResult(False, "visual", detail, "no visual context provided")
    baseline_path = ctx.baselines_dir / a.baseline
    # Flatten any path separators in the baseline key for the in-run copy/diff filenames.
    name = Path(a.baseline).name

    # 1. Preprocess: resolve frames and crop the actual to the scoped element.
    prepared = _prepare_visual_comparison(ctx, a, elements, name)
    if isinstance(prepared, AssertionResult):
        return prepared

    # 2. Baseline: first run (or a brand-new screen) has nothing to compare against. Report the
    # actual (the element crop, when scoped) so it can be reviewed and approved into a baseline.
    if not baseline_path.is_file():
        ev = VisualEvidence(
            baseline_name=a.baseline,
            actual=prepared.actual_rel,
            missing=True,
            element_scoped=prepared.crop is not None,
        )
        return AssertionResult(
            False, "visual", detail, f"baseline not found: {a.baseline}", visual=ev
        )

    try:
        from bajutsu.visual import compare_images
    except ImportError:
        return AssertionResult(
            False, "visual", detail, "visual assertions need the 'visual' extra (Pillow)"
        )

    engine = a.compare or ctx.default_compare
    if engine == "exact" and {"color_tolerance", "antialiasing"} & a.model_fields_set:
        return AssertionResult(
            False,
            "visual",
            detail,
            "colorTolerance/antialiasing are set but the resolved engine is 'exact' "
            "(set compare: pixelmatch or the target's visualCompare)",
        )

    masks_or_result = _resolve_masks(a, elements, prepared.scale, prepared.crop, detail)
    if isinstance(masks_or_result, AssertionResult):
        return masks_or_result
    masks, masked_selectors = masks_or_result

    # 3. Compare: copy the baseline into the run dir, prepare the diff path, run the engine.
    baseline_copy, diff_path = _resolve_baselines(ctx, baseline_path, name)
    result = compare_images(
        prepared.compare_actual,
        baseline_path,
        engine=engine,
        threshold=a.threshold,
        color_tolerance=a.color_tolerance,
        antialiasing=a.antialiasing,
        exclude=masks or None,
        diff_path=diff_path,
    )

    # 4. Build the result and its evidence.
    ev = VisualEvidence(
        baseline_name=a.baseline,
        actual=prepared.actual_rel,
        baseline=_rel(ctx.run_dir, baseline_copy),
        diff=_rel(ctx.run_dir, diff_path) if (not result.ok and diff_path.is_file()) else None,
        diff_pct=result.diff_pct,
        engine=engine,
        element_scoped=prepared.crop is not None,
        masked_selectors=masked_selectors,
    )
    return AssertionResult(result.ok, "visual", detail, result.reason, visual=ev)


def _eval_clipboard(clipboard: str | None, m: ClipboardMatch) -> AssertionResult:
    op = "equals" if m.equals is not None else "matches"
    expected = m.equals if m.equals is not None else m.matches
    detail = f"clipboard {op}={expected!r}"
    if clipboard is None:
        # No device-control channel read the pasteboard (fake driver / parallel run, or the read
        # failed). A clean not-ok, like the visual assertion with no context — never a crash.
        return AssertionResult(
            False, "clipboard", detail, "no clipboard read (device control unavailable)"
        )
    if m.equals is not None:
        ok = clipboard == m.equals
    else:
        assert m.matches is not None
        ok = re.search(m.matches, clipboard) is not None
    reason = "" if ok else f"clipboard was {clipboard!r}, expected {op}={expected!r}"
    return AssertionResult(ok, "clipboard", detail, reason)


def _eval_golden(
    elements: list[base.Element], m: GoldenMatch, ctx: GoldenContext | None
) -> AssertionResult:
    detail = f"golden ≈ {m.path}"
    if ctx is None:
        return AssertionResult(False, "golden", detail, "no golden context provided")
    goldens_dir = ctx.goldens_dir.resolve()
    golden_file = (goldens_dir / m.path).resolve()
    if not golden_file.is_relative_to(goldens_dir):
        return AssertionResult(
            False, "golden", detail, f"golden path escapes the goldens dir: {m.path}"
        )
    if not golden_file.is_file():
        return AssertionResult(False, "golden", detail, f"golden not found: {m.path}")
    from bajutsu.golden import compare_golden, load_golden

    golden = load_golden(golden_file)
    if ctx.screen is not None:
        screen = ctx.screen
    else:
        from bajutsu.elements import screen_size_from_elements

        sw, sh = screen_size_from_elements(elements)
        screen = (0.0, 0.0, sw, sh)
    result = compare_golden(golden, elements, screen)
    if result.ok:
        return AssertionResult(True, "golden", detail)
    parts: list[str] = []
    if result.mismatches:
        parts.append("; ".join(str(mm) for mm in result.mismatches))
    if result.missing:
        parts.append(f"missing: {', '.join(result.missing)}")
    if result.frame_failures:
        parts.append(f"frame failures: {', '.join(result.frame_failures)}")
    return AssertionResult(False, "golden", detail, "; ".join(parts))


def _rel(run_dir: Path, p: Path) -> str:
    """A run-dir-relative POSIX path for the report; falls back to the name if unrelated."""
    try:
        return p.relative_to(run_dir).as_posix()
    except ValueError:
        return p.name


def evaluate_one(
    elements: list[base.Element],
    a: Assertion,
    exchanges: list[NetworkExchange] | None = None,
    *,
    visual_context: VisualContext | None = None,
    schema_context: SchemaContext | None = None,
    clipboard: str | None = None,
    golden_context: GoldenContext | None = None,
) -> AssertionResult:
    """Evaluate one assertion against the screen and the observed network.

    The assertion's kind is guaranteed unique by scenario validation, so exactly one branch fires.
    Evaluation is total — a *failed* check returns a not-ok result rather than raising.

    Args:
        elements: One `query()` snapshot; the UI kinds (`exists` / `value` / `label` / `count` /
            `enabled` / `disabled` / `selected`) check it.
        a: The assertion to evaluate.
        exchanges: The network exchanges observed so far; the `request` / `event` /
            `requestSequence` kinds check these (None is treated as empty).
        visual_context: Paths the `visual` kind needs (screenshot, baselines, diff dir, run dir);
            required only for a `visual` assertion.
        schema_context: The directory a `responseSchema` path resolves against; required only for a
            `responseSchema` assertion.
        clipboard: The device pasteboard text the `clipboard` kind checks; None when unread (no
            device-control channel), which fails the assertion cleanly.
        golden_context: The goldens directory a `golden` assertion resolves against (BE-0006);
            required only for a `golden` assertion.

    Returns:
        The single assertion's result.

    Raises:
        AssertionError: The assertion has no kind set — scenario validation should have caught this.
    """
    if a.exists is not None:
        return _eval_exists(elements, a.exists)
    if a.value is not None:
        return _eval_text(elements, "value", a.value)
    if a.label is not None:
        return _eval_text(elements, "label", a.label)
    if a.count is not None:
        return _eval_count(elements, a.count)
    if a.enabled is not None:
        return _eval_state(elements, "enabled", a.enabled)
    if a.disabled is not None:
        return _eval_state(elements, "disabled", a.disabled)
    if a.selected is not None:
        return _eval_state(elements, "selected", a.selected)
    if a.request is not None:
        return _eval_request(exchanges or [], a.request)
    if a.event is not None:
        return _eval_event(exchanges or [], a.event)
    if a.request_sequence is not None:
        return _eval_request_sequence(exchanges or [], a.request_sequence)
    if a.response_schema is not None:
        return _eval_response_schema(exchanges or [], a.response_schema, schema_context)
    if a.visual is not None:
        return _eval_visual(visual_context, a.visual, elements)
    if a.clipboard is not None:
        return _eval_clipboard(clipboard, a.clipboard)
    if a.golden is not None:
        return _eval_golden(elements, a.golden, golden_context)
    raise AssertionError("empty assertion (should be caught by scenario validation)")


def evaluate(
    elements: list[base.Element],
    assertions: list[Assertion],
    exchanges: list[NetworkExchange] | None = None,
    *,
    visual_context: VisualContext | None = None,
    schema_context: SchemaContext | None = None,
    clipboard: str | None = None,
    golden_context: GoldenContext | None = None,
) -> list[AssertionResult]:
    """Evaluate every assertion in an expect/assert block (the caller AND-s them via `passed`).

    Plain `request` assertions (no `count`) in the block are matched **one-to-one** to distinct
    exchanges: two `request` lines need two separate exchanges. `count` is an explicit aggregate and
    stays independent of that one-to-one assignment.

    Args:
        elements: One `query()` snapshot for the UI kinds.
        assertions: The block's assertions, evaluated in order.
        exchanges: The network exchanges observed so far (None is treated as empty).
        visual_context: Forwarded to any `visual` assertion (see `evaluate_one`).
        schema_context: Forwarded to any `responseSchema` assertion (see `evaluate_one`).
        clipboard: Forwarded to any `clipboard` assertion (see `evaluate_one`).
        golden_context: Forwarded to any `golden` assertion (see `evaluate_one`).

    Returns:
        One result per assertion, positionally aligned with `assertions`.
    """
    exs = exchanges or []
    bare: list[tuple[int, RequestMatch]] = []
    for i, a in enumerate(assertions):
        if a.request is not None and a.request.count is None:
            bare.append((i, a.request))
    assigned: dict[int, AssertionResult] = {}
    if len(bare) >= 2:
        order = _assign_requests(exs, [req for _, req in bare])
        for (i, req), ex_idx in zip(bare, order, strict=True):
            assigned[i] = _request_assignment_result(req, ex_idx, exs)
    return [
        assigned[i]
        if i in assigned
        else evaluate_one(
            elements,
            a,
            exs,
            visual_context=visual_context,
            schema_context=schema_context,
            clipboard=clipboard,
            golden_context=golden_context,
        )
        for i, a in enumerate(assertions)
    ]


def passed(results: list[AssertionResult]) -> bool:
    """True iff every assertion is ok (AND; one failure fails the step)."""
    return all(r.ok for r in results)
