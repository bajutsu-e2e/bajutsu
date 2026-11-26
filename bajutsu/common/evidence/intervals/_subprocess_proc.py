"""The real child process behind an interval capture."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

# Cadence and read size for that stderr wait. Both are host-local reads on an unlinked temp file, so
# the poll is effectively free and the cadence is set by how precisely `true_start` should be
# resolved rather than by what the probe costs.
_STDERR_POLL = 0.05
_STDERR_READ_SIZE = 65536


class _SubprocessProc:
    def __init__(self, argv: list[str], stdout_path: Path | None) -> None:
        self._file = stdout_path.open("wb") if stdout_path is not None else None
        # A temporary file rather than a pipe: nobody drains a recorder's stderr for the minutes it
        # runs, and a full pipe buffer would block the child mid-recording. The file is unlinked on
        # creation, so it needs no cleanup beyond the close in `stop()`.
        self._err = tempfile.TemporaryFile()  # noqa: SIM115  # closed in stop(), not a with-block
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=self._file if self._file is not None else subprocess.DEVNULL,
                stderr=self._err,
            )
        except BaseException:
            self._err.close()
            if self._file is not None:
                self._file.close()
            raise

    def await_stderr(self, needle: str, timeout: float) -> float | None:
        """Wait for `needle` on the child's stderr; the instant it appeared, or None on timeout.

        Reads with `os.pread` so the child's own write offset is never disturbed, and carries one
        needle-width tail between reads so a match straddling two reads is still seen. A condition
        wait to a bounded deadline (prime directive 2), and always at least one read: a child that
        answered before the first poll must not be failed by a zero-length timeout.
        """
        deadline = time.monotonic() + timeout
        target = needle.encode()
        offset = 0
        tail = b""
        while True:
            chunk = os.pread(self._err.fileno(), _STDERR_READ_SIZE, offset)
            if chunk:
                offset += len(chunk)
                window = tail + chunk
                if target in window:
                    return time.monotonic()
                tail = window[1 - len(target) :] if len(target) > 1 else b""
            if time.monotonic() >= deadline:
                return None
            time.sleep(_STDERR_POLL)

    def await_stdout(self, timeout: float) -> float | None:
        """Wait for the child's first byte in its stdout file; that instant, or None.

        None on timeout, when the child exits having written nothing (no point waiting out the
        deadline), and when stdout goes to no file. Reads at least once, as `await_stderr` does.
        """
        if self._file is None:
            return None
        deadline = time.monotonic() + timeout
        while True:
            # Sampled before the size, so output written just before an exit is still seen.
            exited = self._proc.poll() is not None
            if os.fstat(self._file.fileno()).st_size:
                return time.monotonic()
            if exited or time.monotonic() >= deadline:
                return None
            time.sleep(_STDERR_POLL)

    def stop(self, sig: int, timeout: float) -> None:
        self._proc.send_signal(sig)
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._err.close()
        if self._file is not None:
            self._file.close()
