"""The error raised when the resident channel took too long to answer."""

from __future__ import annotations

from .adb_resident_error import AdbResidentError


class AdbResidentTimeout(AdbResidentError):
    """The resident channel did not answer in time, which does not show that the server is gone.

    A refused connection or a closed socket says the server cannot answer; a timeout says only that it
    has not yet. The server may still be working on the request, so the lease tries to recover before
    it gives the channel up.
    """
