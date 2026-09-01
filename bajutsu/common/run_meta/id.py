"""The run-id format, named once (BE-0200).

A run id is a zero-padded UTC timestamp — `20260613-153045` — and that exact shape is a
cross-surface contract, not a local detail: the Web UI sorts run history lexicographically
(chronological *only because* the ids are zero-padded UTC timestamps), and `report/ctrf.py`
parses the shape back into a run start time. Minting it independently at each call site let a
well-meaning tweak (a timezone, a precision bump, a separator change) at one site silently
break ordering or parsing at another. This module owns the format so it changes in one place.

Being a pure timestamp helper, it carries no LLM and no verdict — it stays firmly on the
deterministic side of the run/CI contract.

`valid_run_id` in `serve/helpers.py` is a *different* thing and stays separate: it's a
path-safety check that intentionally accepts client-supplied ids that aren't timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime

# The single source of truth for the run-id timestamp shape. `strftime`/`strptime` compatible.
RUN_ID_FORMAT = "%Y%m%d-%H%M%S"


def new_run_id(prefix: str = "") -> str:
    """Mint a run id: the current UTC time in `RUN_ID_FORMAT`, optionally prefixed.

    Args:
        prefix: Prepended verbatim (e.g. ``"audit-"`` for `bajutsu audit`). A prefixed id is no
            longer a bare timestamp, so `parse_run_id_timestamp` returns None for it.
    """
    return f"{prefix}{datetime.now(tz=UTC).strftime(RUN_ID_FORMAT)}"


def parse_run_id_timestamp(run_id: str) -> datetime | None:
    """The UTC-aware start time encoded in *run_id*, or None if it isn't a bare timestamp.

    Run ids are stamped in UTC, so they parse as UTC — exact to the second. A non-timestamp id
    (a test id, or a prefixed one like ``audit-…``) yields None rather than a fabricated time.
    """
    try:
        return datetime.strptime(run_id, RUN_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
