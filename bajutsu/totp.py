"""RFC 6238 time-based one-time password (TOTP), used by the `totp` step (BE-0046).

A pure, deterministic function of the shared secret and the time: the same `(secret, now)` always
yields the same code, so it brings a 2FA value into a run without an LLM or a scripting escape
hatch. No network, no device — the only input beyond the secret is the clock.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct


def totp(secret: str, *, now: float, digits: int = 6, period: int = 30) -> str:
    """The RFC 6238 TOTP for `secret` at Unix time `now`.

    Args:
        secret: The shared key as base32 (case-insensitive; spaces are ignored, as authenticator
            apps group and lower-case it).
        now: Unix time in seconds; the code is constant within each `period`-second window.
        digits: Length of the returned code, zero-padded.
        period: Window length in seconds (the RFC default is 30).

    Returns:
        The zero-padded numeric code.

    Raises:
        ValueError: `secret` is not valid base32.
    """
    key = _decode_base32(secret)
    counter = int(now // period)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # Dynamic truncation (RFC 4226 §5.3): the low nibble of the last byte picks a 4-byte window.
    offset = mac[-1] & 0x0F
    code = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def _decode_base32(secret: str) -> bytes:
    """Decode a user-entered base32 secret, tolerating spaces, lower case, and missing padding."""
    cleaned = secret.replace(" ", "").upper()
    cleaned += "=" * (-len(cleaned) % 8)  # b32decode requires the padding authenticators omit
    return base64.b32decode(cleaned, casefold=True)
