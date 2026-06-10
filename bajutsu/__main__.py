"""Enable `python -m bajutsu ...` (used by `triage --rerun` to re-run a patched scenario)."""

from __future__ import annotations

from bajutsu.cli import app

if __name__ == "__main__":
    app()
