"""Guard: composite-action `run:` blocks must not inline-expand `${{ inputs.* }}` (BE-0123).

GitHub Actions expands `${{ ... }}` textually, before the shell parses the line, so an input
spliced straight into a `run:` script is a latent shell-injection vector the moment a caller
forwards attacker-influenced text. The fix is `env:` indirection — map the input to a shell
variable and reference `"$VAR"` — so the shell sees data, not script. This test fails if a
composite action's `run:` block reintroduces a raw `${{ inputs. }}` expansion.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ACTIONS_DIR = Path(__file__).resolve().parent.parent / ".github" / "actions"

# Tolerate whitespace: `${{ inputs.x }}`, `${{inputs.x}}`, `${{  inputs.x}}` all match.
_INLINE_INPUT = re.compile(r"\$\{\{\s*inputs\.")


def _composite_action_files() -> list[Path]:
    return sorted(_ACTIONS_DIR.glob("*/action.yml"))


def _inline_input_steps(action_file: Path) -> list[str]:
    """Names of steps whose `run:` block inline-expands `${{ inputs.* }}`."""
    action = yaml.safe_load(action_file.read_text())
    steps = action.get("runs", {}).get("steps", [])
    return [
        step.get("name", "<unnamed>")
        for step in steps
        if isinstance(step.get("run"), str) and _INLINE_INPUT.search(step["run"])
    ]


def test_composite_actions_exist() -> None:
    # Guard the guard: if the actions move, this test must be updated, not silently pass.
    assert _composite_action_files(), "no composite action.yml files found under .github/actions/"


def test_no_inline_input_expansion_in_run_blocks() -> None:
    offenders = {
        str(action_file.relative_to(_ACTIONS_DIR.parent.parent)): steps
        for action_file in _composite_action_files()
        if (steps := _inline_input_steps(action_file))
    }
    assert not offenders, (
        "composite-action `run:` blocks must route inputs through `env:` indirection, "
        f"not inline `${{{{ inputs.* }}}}` (BE-0123); offending steps: {offenders}"
    )
