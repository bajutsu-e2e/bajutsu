"""Shared scenario walk for the codegen emitters (BE-0083).

XCUITest (`xcuitest.py`) and Playwright (`playwright.py`) transpile a scenario the same way
— merge the launch environment, open the test, emit a launch line, emit each step, then the
`expect` block, and close — differing only in the per-line target syntax. That walk lives here
once; each target supplies the variable parts through the `CodeGenerator` protocol, so adding a
third target (e.g. an Android emitter) is the cost of its line syntax alone, not another copy of
the skeleton.

This is a pure, deterministic transform: no AI, no device. The per-line builders (`step_lines` /
`assertion_lines` and the selector/locator helpers behind them) stay in each target's module.
"""

from ._functions import _BEFORE_COMMENT as _BEFORE_COMMENT
from ._functions import _BODY_INDENT as _BODY_INDENT
from ._functions import _EXPECT_COMMENT as _EXPECT_COMMENT
from ._functions import _LINE_TERMINATORS as _LINE_TERMINATORS
from ._functions import _RE_METACHARS as _RE_METACHARS
from ._functions import _RUNTIME_ONLY_HINT as _RUNTIME_ONLY_HINT
from ._functions import _collapse_line_terminators as _collapse_line_terminators
from ._functions import _reject_runtime_only as _reject_runtime_only
from ._functions import _scenario_lines as _scenario_lines
from ._functions import (
    class_name,
    device_group_todo,
    ident,
    indent_lines,
    interrupts_setup_lines,
    is_plain_substring,
    manual_todo,
    ms,
    network_unsupported,
    permissions_setup_lines,
    render_test_file,
    sleep_comment,
)
from .after_emission import AfterEmission
from .code_generator import CodeGenerator
from .codegen_error import CodegenError

__all__ = [
    "AfterEmission",
    "CodeGenerator",
    "CodegenError",
    "class_name",
    "device_group_todo",
    "ident",
    "indent_lines",
    "interrupts_setup_lines",
    "is_plain_substring",
    "manual_todo",
    "ms",
    "network_unsupported",
    "permissions_setup_lines",
    "render_test_file",
    "sleep_comment",
]
