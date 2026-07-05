"""Shared emit dispatch for `codegen`: scenario model -> (source, filename) for a target format.

The one place that knows which emit formats exist, that Playwright needs a web target, and which
generator + filename each format uses. Both transports — the `codegen` CLI command and the serve
`/api/codegen` endpoint (BE-0137) — call `generate_test` so they can never disagree about what
`--emit` means; each only translates a `CodegenError` into its own error surface."""

from __future__ import annotations

from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.codegen_playwright import describe_name_for, to_playwright
from bajutsu.config import Effective, web_base_url
from bajutsu.scenario import Scenario

# The emit formats `codegen` supports; the order the CLI's `--emit` help lists them in.
EMIT_TARGETS = ("xcuitest", "playwright")


class CodegenError(ValueError):
    """A codegen request that cannot be fulfilled: an unknown emit, or Playwright on a non-web target."""


def generate_test(
    emit: str, scenarios: list[Scenario], stem: str, eff: Effective
) -> tuple[str, str]:
    """Generate native test source and its filename for *emit*.

    Args:
        stem: The scenario's file stem, used to name the emitted test (its class / describe block
            and the returned filename).

    Raises:
        CodegenError: *emit* is not a known format, or it is ``playwright`` on a target with no
            web base URL.
    """
    if emit not in EMIT_TARGETS:
        raise CodegenError(f"unsupported emit: {emit} (one of {', '.join(EMIT_TARGETS)})")
    if emit == "playwright":
        base_url = web_base_url(eff)
        if not base_url:
            raise CodegenError("playwright codegen needs a web target (baseUrl)")
        code = to_playwright(scenarios, describe_name_for(stem), base_url, eff.launch_env)
        return code, f"{stem}.spec.ts"
    class_name = class_name_for(stem)
    return to_xcuitest(scenarios, class_name, eff.launch_env), f"{class_name}.swift"
