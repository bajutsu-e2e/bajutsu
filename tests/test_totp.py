"""Tests for the RFC 6238 TOTP generator (bajutsu/totp.py, BE-0046).

Pure and device-free: the code is a deterministic function of the shared secret and the time, so
it is checked against the RFC 6238 test vectors with no clock and no external service.
"""

from __future__ import annotations

import pytest

from bajutsu.totp import totp

# RFC 6238 Appendix B uses the ASCII secret "12345678901234567890" (base32 below); its published
# 8-digit codes truncate to these 6-digit codes at the listed Unix times.
_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("now", "code"),
    [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924")],
)
def test_rfc6238_vectors(now: int, code: str) -> None:
    assert totp(_SECRET, now=now) == code


def test_same_within_a_period_changes_across_periods() -> None:
    # 30 and 59 share the 30s window (counter 1); 60 starts the next (counter 2).
    assert totp(_SECRET, now=30) == totp(_SECRET, now=59)
    assert totp(_SECRET, now=30) != totp(_SECRET, now=60)


def test_code_is_zero_padded_to_the_requested_digits() -> None:
    code = totp(_SECRET, now=59)
    assert len(code) == 6 and code.isdigit()


def test_secret_is_case_and_space_insensitive() -> None:
    # Authenticator secrets are often shown lower-cased and space-grouped; accept both.
    assert totp("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", now=59) == totp(_SECRET, now=59)


def test_invalid_base32_secret_raises_value_error() -> None:
    with pytest.raises(ValueError, match="base32"):
        totp("not!base32", now=59)
