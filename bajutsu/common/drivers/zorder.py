"""Z-order responder client — Python side of the BajutsuKit `nativeZ` channel (BE-0355).

Asks the app under test where each of its own elements sits front to back, so a driver can carry
the answer into `Element.nativeZ`. An app that never links the responder simply refuses the
connection, and the reader reports the honest absence that refusal means.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Protocol

from bajutsu.common.drivers.base import native_z_from_json


class ZOrderSource(Protocol):
    """What a driver needs of the responder: identifier to measured position, or nothing."""

    def positions(self) -> dict[str, float]: ...


class ZOrderResponder:
    """HTTP client for the BajutsuKit in-app z-order responder."""

    def __init__(self, port: int, token: str, host: str = "127.0.0.1") -> None:
        self.port = port  # the host port this responder reserved — one per lease
        self.token = token
        self._base_url = f"http://{host}:{port}"
        # An app with no responder refuses the connection, and it will refuse every later one the
        # same way, so the first failure settles the question for the rest of the lease.
        self._unavailable = False

    def positions(self) -> dict[str, float]:
        """Each identified element's front-to-back position, or empty when the app reports none.

        The units are the backend's own (an ordinal over the real compositing order on iOS), so the
        only claim that holds across backends is that a larger value is closer to the viewer.
        """
        if self._unavailable:
            return {}
        req = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/zorder",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # 401/404 mean a server answered but will never accept this token or route — that
            # never resolves itself, so it latches like a refused connection. Any other status
            # (503 on a busy main thread, say) is a hiccup from a responder that demonstrably
            # exists, and the next query should still ask it rather than being latched off by one
            # bad moment.
            if e.code in (401, 404):
                self._unavailable = True
            return {}
        except (
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,
            UnicodeDecodeError,
        ):
            # `http.client.HTTPException` (a malformed status line, a body shorter than its own
            # `Content-Length`) and `UnicodeDecodeError` (a non-UTF-8 body `json.loads` cannot even
            # start parsing) are neither `OSError` and would otherwise slip past every clause
            # below. Neither is necessarily "no responder" either — the app terminating mid-reply
            # at scenario end produces exactly a truncated or garbled body — so this degrades the
            # one read rather than latching off the rest of the lease (`fetch_source` in
            # `bajutsu/common/backend_cli/adb_resident.py` catches both for the same reason).
            return {}
        except (urllib.error.URLError, OSError) as exc:
            # No app-side responder at all (connection refused, or nothing ever listened) — this
            # will not change for the rest of the lease. A *connect*-phase timeout arrives here
            # too, wrapped in `URLError` by urllib's own `do_open` (a read-phase one raises the
            # bare `TimeoutError` the clause above already catches) — and that one is a hiccup
            # from a responder that may well exist (the app still coming up), so it degrades this
            # read rather than latching off the lease.
            if isinstance(getattr(exc, "reason", exc), TimeoutError):
                return {}
            self._unavailable = True
            return {}
        # A malformed top-level shape (loopback is not isolated between apps, so this need not be
        # bajutsu's own responder) reports the same absence as an app that never opted in, rather
        # than raising past this diagnostic read into the caller's own element query.
        elements = data.get("elements") if isinstance(data, dict) else None
        if not isinstance(elements, list):
            elements = []
        found: dict[str, float] = {}
        repeated: set[str] = set()
        for record in elements:
            if not isinstance(record, dict):
                continue
            identifier = record.get("identifier")
            z = native_z_from_json(record.get("nativeZ"))
            if not isinstance(identifier, str) or z is None:
                continue
            if identifier in found:
                repeated.add(identifier)
            found[identifier] = z
        # An identifier the app repeats names no single element, so neither reading is that
        # element's own position — drop it rather than hand one element the other's.
        return {k: v for k, v in found.items() if k not in repeated}
