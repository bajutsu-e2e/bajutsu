"""The error naming the app under test's own abnormal termination (BE-0424)."""

from __future__ import annotations


class AppCrashedError(RuntimeError):
    """The app under test itself terminated abnormally mid-scenario.

    Distinct from `BackendCrashError`: the backend's own driver process (the resident XCUITest
    runner, an adb resident server) is healthy and answering — only the app being tested is gone.
    Raised only where a driver has positively confirmed this event, never inferred from an ordinary
    `ElementNotFound`. Constructed and consumed entirely within the step loop's reactive check
    (`_StepRunner._finish_outcome`), for its message text alone: it never escapes to `pipeline.py`,
    so it carries none of `BackendCrashError`'s recovery semantics — an app crash leaves the driver,
    the backend process, and the running video recording all intact — and needs no shared base class
    with it.
    """
