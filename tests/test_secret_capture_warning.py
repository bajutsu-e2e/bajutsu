"""Tests for the on-screen-secret capture warning (BE-0151).

`record` sends the live screenshot to the AI each turn; `triage --ai` sends the captured failure
screenshot from the run's `runs/` evidence. Either way, a value the app displays on screen is not
redacted from those images. `_warn_onscreen_secrets` discloses that at the moment secrets are
bound; it is a disclosure, not a behavior change (no LLM, no effect on `run`/CI).
"""

from __future__ import annotations

from bajutsu.cli._shared import _warn_onscreen_secrets
from bajutsu.config import load_config, resolve

_WITH_SECRETS = """
targets:
  app:
    bundleId: com.example.app
    secrets: [LOGIN_PASSWORD, LOGIN_OTP]
"""

_NO_SECRETS = """
targets:
  app:
    bundleId: com.example.app
"""


def test_warns_when_secrets_bound(capsys) -> None:
    eff = resolve(load_config(_WITH_SECRETS), "app")
    _warn_onscreen_secrets(eff)
    captured = capsys.readouterr()
    err = captured.err
    # The warning names the boundary precisely: images are never redacted, the screenshot reaches
    # the AI provider, and it distinguishes the two callers — and it names the bound secrets.
    assert "screenshot" in err
    assert "provider" in err
    assert "record" in err and "triage --ai" in err
    assert "LOGIN_PASSWORD" in err and "LOGIN_OTP" in err
    # It also states what IS masked, so the author does not read it as "secrets leak everywhere".
    assert "text evidence" in err
    # A disclosure belongs on stderr, leaving stdout for the command's own result output.
    assert captured.out == ""


def test_silent_when_no_secrets_bound(capsys) -> None:
    eff = resolve(load_config(_NO_SECRETS), "app")
    _warn_onscreen_secrets(eff)
    assert capsys.readouterr().err == ""
