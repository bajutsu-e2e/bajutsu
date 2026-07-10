"""The ``/metrics`` observability endpoint (BE-0169).

Renders Prometheus text-exposition metrics from state the control plane already tracks — the local
`state.jobs` (in-flight jobs), and, when a database is wired (the server backend), the jobs table's
queue depth, leased jobs, and worker heartbeat freshness. It reads existing state and adds no
bookkeeping. The output carries only counts, ages, and org / worker identifiers — never a job spec,
a result, or the operator token — so a scrape can never leak a secret (BE-0055).
"""

from __future__ import annotations

from bajutsu.serve.state import ServeState

# The Prometheus text format's own content type (version 0.0.4); a scraper keys off it.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def render_metrics(state: ServeState) -> tuple[str, int]:
    """The metrics page in Prometheus text format, and HTTP 200.

    Args:
        state: The serve state to read counts from. A wired ``repository`` (the server backend) adds
            the queue / lease / worker series; without one (local serve) only the in-flight and
            capacity series appear.

    Returns:
        The rendered exposition text and status code, shaped like the other operations.
    """
    lines: list[str] = []

    _gauge(
        lines,
        "bajutsu_in_flight_jobs",
        "Jobs the control plane is running, by org.",
        state.in_flight_by_org(),
    )
    _scalar(
        lines,
        "bajutsu_max_concurrent",
        "Configured cap on concurrent jobs (0 = unlimited).",
        state.max_concurrent,
    )

    if state.repository is not None:
        snap = state.repository.metrics_snapshot()
        _gauge(
            lines, "bajutsu_queue_depth", "Jobs waiting in the queue, by org.", snap.queued_by_org
        )
        _gauge(
            lines,
            "bajutsu_leased_jobs",
            "Jobs leased to a worker (in flight), by org.",
            snap.leased_by_org,
        )
        _gauge(
            lines,
            "bajutsu_worker_heartbeat_age_seconds",
            "Seconds since a worker's last heartbeat; past the lease timeout means a dead worker.",
            snap.heartbeat_age_by_worker,
            label="worker",
        )
        _scalar(
            lines,
            "bajutsu_oldest_in_flight_seconds",
            "Seconds since the oldest in-flight job was enqueued (includes time queued) —"
            " a slow-run signal.",
            snap.oldest_in_flight_seconds,
        )
        _scalar(
            lines,
            "bajutsu_unroutable_jobs",
            "Queued jobs no live worker can serve (their required capabilities match no worker) —"
            " add a worker with the missing capability (BE-0166).",
            snap.unroutable_queued,
        )

    return "\n".join(lines) + "\n", 200


def _gauge(
    lines: list[str],
    name: str,
    help_text: str,
    values: dict[str, int] | dict[str, float],
    *,
    label: str = "org",
) -> None:
    """Append a labelled gauge metric family. An empty family still emits its ``HELP``/``TYPE``
    header so the metric is documented even when no series are present."""
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    for key, value in values.items():
        lines.append(f'{name}{{{label}="{_escape(key)}"}} {_num(value)}')


def _scalar(lines: list[str], name: str, help_text: str, value: int | float) -> None:
    """Append a single label-less gauge (its ``HELP``/``TYPE`` header and one value line)."""
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {_num(value)}")


def _num(value: int | float) -> str:
    """Render a metric value: integers verbatim, floats compactly (``%g``)."""
    return str(value) if isinstance(value, int) else f"{value:g}"


def _escape(value: str) -> str:
    """Escape a Prometheus label value: backslash, double-quote, and newline (per the text format)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
