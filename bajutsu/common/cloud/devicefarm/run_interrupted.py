"""The error raised when the caller interrupts a Device Farm run before its verdict is collected."""

from __future__ import annotations

from typing import Literal

from .device_farm_error import DeviceFarmError

# What a caller asks of a run it no longer waits for (BE-0448): ``stop`` ends the scheduled run
# through `stop_run`, since nobody will collect it (a cancel); ``leave`` lets it run on, since
# another worker that holds the job now resumes it from the stored ARN (a lost lease).
Interrupt = Literal["stop", "leave"]


class RunInterrupted(DeviceFarmError):
    """The caller interrupted the submission or the poll, so no verdict was collected.

    Attributes:
        interrupt: What the caller asked for.
        run_arn: The run that was scheduled, or None when the interrupt came before scheduling.
    """

    def __init__(self, interrupt: Interrupt, run_arn: str | None) -> None:
        if run_arn is None:
            message = "interrupted before the run was scheduled"
        elif interrupt == "stop":
            message = f"interrupted: stopped run {run_arn}"
        else:
            message = f"interrupted: left run {run_arn} to the worker that holds the job now"
        super().__init__(message)
        self.interrupt: Interrupt = interrupt
        self.run_arn = run_arn
