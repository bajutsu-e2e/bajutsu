"""Tests for the host addresses a real iOS device is offered for reaching the network collector."""

from __future__ import annotations

import pytest

from bajutsu.common.platform_lifecycle.environments.xcuitest._host_address import (
    HOST_ADDRESS_ENV,
    HostAddressError,
    host_candidates,
    interface_addresses,
)
from bajutsu.common.platform_lifecycle.protocols import CollectorHost

_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
\tinet6 ::1 prefixlen 128
\tinet6 fe80::1%lo0 prefixlen 64 scopeid 0x1
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 3c:22:fb:00:00:00
\tinet6 fe80::1c2b:aaaa:bbbb:cccc%en0 prefixlen 64 secured scopeid 0xb
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
\tinet6 2001:db8::20 prefixlen 64 autoconf secured
en5: flags=8822<BROADCAST,SMART,SIMPLEX,MULTICAST> mtu 1500
\tinet 10.9.9.9 netmask 0xff000000
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 16000
\tinet6 fd71:6e2a:1b4c::1 prefixlen 64
"""


def test_interface_addresses_keep_routable_addresses_of_interfaces_that_are_up() -> None:
    # Loopback and link-local are dropped, a down interface (en5) contributes nothing, IPv4 comes
    # first, and the tunnel's IPv6 (the CoreDevice route) stays in.
    assert interface_addresses(_IFCONFIG) == (
        "192.168.1.20",
        "2001:db8::20",
        "fd71:6e2a:1b4c::1",
    )


def test_interface_addresses_of_an_empty_table_are_empty() -> None:
    assert interface_addresses("") == ()


def test_the_environment_overrides_the_config() -> None:
    found = host_candidates(
        "10.0.0.2", environ={HOST_ADDRESS_ENV: "172.16.0.5, 172.16.0.6"}, read_interfaces=str
    )
    assert found.addresses == ("172.16.0.5", "172.16.0.6")
    assert found.source == HOST_ADDRESS_ENV


def test_the_config_overrides_the_interfaces() -> None:
    def _no_ifconfig() -> str:
        pytest.fail("an explicit address must not read the interface table")

    found = host_candidates("10.0.0.2", environ={}, read_interfaces=_no_ifconfig)
    assert found.addresses == ("10.0.0.2",)
    assert found.source == "xcuitest.hostAddress"


def test_without_an_explicit_address_the_interfaces_are_offered() -> None:
    found = host_candidates(
        None, environ={HOST_ADDRESS_ENV: " "}, read_interfaces=lambda: _IFCONFIG
    )
    assert found.addresses[0] == "192.168.1.20"
    assert found.source == "host interfaces"


def test_collector_env_lists_one_url_per_address() -> None:
    host = CollectorHost(bind="::", advertised=("192.168.1.20", "fd00::1"))
    assert host.collector_env(4100) == "http://192.168.1.20:4100,http://[fd00::1]:4100"
    assert CollectorHost().collector_env(4100) == "http://127.0.0.1:4100"


def test_an_explicit_entry_is_normalized_or_refused_naming_its_source() -> None:
    found = host_candidates("[fd00::1], build-host.local", environ={}, read_interfaces=str)
    assert found.addresses == ("fd00::1", "build-host.local")
    for bad in ("192.168.1.20:8080", "fe80::1%en0", "fe80::1", "10.0.0.0/8"):
        with pytest.raises(HostAddressError, match=r"xcuitest\.hostAddress"):
            host_candidates(bad, environ={}, read_interfaces=str)
    with pytest.raises(HostAddressError, match=HOST_ADDRESS_ENV):
        host_candidates(None, environ={HOST_ADDRESS_ENV: "1.2.3.4:5"}, read_interfaces=str)


def test_a_failed_interface_read_keeps_its_reason() -> None:
    def _broken() -> str:
        raise OSError("ifconfig failed: not found")

    found = host_candidates(None, environ={}, read_interfaces=_broken)
    assert found.addresses == ()
    assert "ifconfig failed: not found" in found.source
