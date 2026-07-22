"""Shared emit dispatch for `codegen`: scenario model -> (source, filename) for a target format.

The one place that knows which emit formats exist, that Playwright needs a web target, and which
generator + filename each format uses. Both transports — the `codegen` CLI command and the serve
`/api/codegen` endpoint (BE-0137) — call `generate_test` so they can never disagree about what
`--emit` means; each only translates a `CodegenError` into its own error surface."""

from __future__ import annotations

from bajutsu.codegen.common import CodegenError
from bajutsu.codegen.playwright import describe_name_for, to_playwright
from bajutsu.codegen.uiautomator import class_name_for as uiautomator_class_name_for
from bajutsu.codegen.uiautomator import to_uiautomator
from bajutsu.codegen.xcuitest import class_name_for, to_xcuitest
from bajutsu.config import Effective, android_package, web_base_url
from bajutsu.scenario import Scenario

# `CodegenError` now lives in `common` (the shared walk raises it too, BE-0297); re-exported here so
# `bajutsu.codegen.emit.CodegenError` — the path both transports import — is unchanged.
__all__ = ["EMIT_TARGETS", "CodegenError", "generate_test"]

# The emit formats `codegen` supports; the order the CLI's `--emit` help lists them in.
EMIT_TARGETS = ("xcuitest", "playwright", "uiautomator")


def generate_test(
    emit: str, scenarios: list[Scenario], stem: str, eff: Effective
) -> tuple[str, str]:
    """Generate native test source and its filename for *emit*.

    Args:
        stem: The scenario's file stem, used to name the emitted test (its class / describe block
            and the returned filename).

    Raises:
        CodegenError: *emit* is not a known format, it is ``playwright`` on a target with no web
            base URL, ``uiautomator`` on a target with no Android package, or a scenario uses a
            runtime-only construct no target can translate (``if`` / ``forEach`` / ``extract``).
    """
    if emit not in EMIT_TARGETS:
        raise CodegenError(f"unsupported emit: {emit} (one of {', '.join(EMIT_TARGETS)})")
    if emit == "playwright":
        base_url = web_base_url(eff)
        if not base_url:
            raise CodegenError("playwright codegen needs a web target (baseUrl)")
        code = to_playwright(scenarios, describe_name_for(stem), base_url, eff.launch_env)
        return code, f"{stem}.spec.ts"
    if emit == "uiautomator":
        package = android_package(eff)
        if not package:
            raise CodegenError("uiautomator codegen needs an Android target (package)")
        class_name = uiautomator_class_name_for(stem)
        code = to_uiautomator(scenarios, class_name, package, eff.launch_env)
        return code, f"{class_name}.kt"
    class_name = class_name_for(stem)
    return to_xcuitest(scenarios, class_name, eff.launch_env), f"{class_name}.swift"
