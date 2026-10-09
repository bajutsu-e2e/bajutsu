"""The host addresses a real iOS device is offered for reaching the network collector.

The collector is the one channel the app opens to the host. A Simulator reaches it on the shared
loopback; a real device cannot, and usbmuxd carries connections from the host to the device only. So
the run offers the app a list of host addresses, and the app keeps the first one that answers its
authenticated probe. An explicit address narrows the list: from the environment first, so a host
whose address changes per job (a device-cloud host) overrides the config without editing it.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

HOST_ADDRESS_ENV = "BAJUTSU_HOST_ADDRESS"


class HostAddressError(ValueError):
    """An explicit host address the app could not use as a collector URL's host."""


@dataclass(frozen=True)
class HostCandidates:
    """The addresses to offer, and where they came from (for diagnostics)."""

    addresses: tuple[str, ...]
    source: str


def _split(raw: str, source: str) -> tuple[str, ...]:
    return tuple(_checked(part.strip(), source) for part in raw.split(",") if part.strip())


def _checked(entry: str, source: str) -> str:
    """`entry` as a bare IP literal or hostname, or a loud error naming the entry and its source.

    A typo here would otherwise become a URL the app silently drops, and the run would show only an
    empty network record. A bracketed IPv6 literal is accepted and unbracketed; a port, a zone, or
    anything else with a colon that is not an IP literal is refused.
    """
    bare = entry.removeprefix("[").removesuffix("]")
    try:
        addr = ipaddress.ip_address(bare)
    except ValueError:
        if ":" in bare or "/" in bare or "%" in bare or not bare:
            raise HostAddressError(
                f"{source}: {entry!r} is not an IP address or a host name (no port or zone)"
            ) from None
        return bare  # a host name the device resolves itself
    if addr.is_link_local:
        raise HostAddressError(f"{source}: {entry!r} is link-local; the app cannot name its zone")
    return str(addr)


def _ifconfig() -> str:
    """The host's interface table.

    Raises:
        OSError: `ifconfig` is missing or failed, with the reason.
    """
    try:
        proc = subprocess.run(
            ["ifconfig"],  # noqa: S607 — the host's own interface table; macOS ships it on PATH
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"ifconfig failed: {exc}") from exc
    return proc.stdout


def interface_addresses(ifconfig_output: str) -> tuple[str, ...]:
    """Every routable address of an interface that is up, IPv4 first, from `ifconfig` output.

    Loopback and link-local addresses are dropped: the device cannot reach the first, and the second
    needs a scope the app has no way to name. IPv6 stays in, because the CoreDevice tunnel a
    real-device test already rides is IPv6, and it may be the one route from the device to the host.
    """
    v4: list[str] = []
    v6: list[str] = []
    up = False
    for line in ifconfig_output.splitlines():
        if line and not line[0].isspace():  # a new interface header: `en0: flags=8863<UP,...>`
            flags = line.partition("<")[2].partition(">")[0].split(",")
            up = "UP" in flags and "RUNNING" in flags
            continue
        fields = line.split()
        if not up or len(fields) < 2 or fields[0] not in ("inet", "inet6"):
            continue
        try:
            addr = ipaddress.ip_address(fields[1].partition("%")[0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            continue
        (v4 if addr.version == 4 else v6).append(str(addr))
    return tuple(dict.fromkeys(v4 + v6))


def host_candidates(
    configured: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    read_interfaces: Callable[[], str] | None = None,
) -> HostCandidates:
    """The host addresses to offer a real device, by precedence: environment, config, interfaces.

    Raises:
        HostAddressError: an explicit entry is not a usable IP address or host name.
    """
    env = os.environ if environ is None else environ
    if explicit := _split(env.get(HOST_ADDRESS_ENV, ""), HOST_ADDRESS_ENV):
        return HostCandidates(explicit, HOST_ADDRESS_ENV)
    if configured and (explicit := _split(configured, "xcuitest.hostAddress")):
        return HostCandidates(explicit, "xcuitest.hostAddress")
    try:
        table = (read_interfaces or _ifconfig)()
    except OSError as exc:
        # Kept in the source, so "none found" says why instead of implying the host has no address.
        return HostCandidates((), f"host interfaces ({exc})")
    return HostCandidates(interface_addresses(table), "host interfaces")
