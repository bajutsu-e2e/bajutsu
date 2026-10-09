"""Where the network collector binds and which addresses the app is told to reach it on."""

from __future__ import annotations

from dataclasses import dataclass

LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class CollectorHost:
    """The collector's bind address and the host addresses the app is offered, in preference order.

    The loopback on both sides wherever the app shares the host's loopback, or a tunnel makes it look
    so (the Simulator, Android's `adb reverse`). A real iOS device does not, so it binds every
    interface and offers the app the host's routable addresses to probe.
    """

    bind: str = LOOPBACK
    advertised: tuple[str, ...] = (LOOPBACK,)

    def collector_env(self, port: int) -> str:
        """`BAJUTSU_COLLECTOR`: one URL per advertised address, comma-separated, in preference order.

        A single address keeps the one-URL value every app already parses.
        """
        return ",".join(
            f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
            for host in self.advertised
        )
