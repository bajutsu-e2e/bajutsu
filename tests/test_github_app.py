"""Minting a GitHub App installation token for the private-repo config source (bajutsu/github_app.py, BE-0224).

The App-level API calls are injected (the `fetch` seam), so the token flow tests offline; the RS256
signature is exercised for real against a generated RSA key (`cryptography` is in the dev group via
the `db`/`githubapp` extras).
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from bajutsu.config_source import GitConfigSpec, GitHubAccessError
from bajutsu.github_app import _app_jwt, _fetch, installation_token

_SPEC = GitConfigSpec("github.com", "acme", "repo", None, None)


def _rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")


def _unb64(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def test_app_jwt_has_expected_claims_and_a_valid_signature() -> None:
    pem = _rsa_pem()
    jwt = _app_jwt("12345", pem, now=1_000_000)
    header_seg, claims_seg, sig_seg = jwt.split(".")

    assert json.loads(_unb64(header_seg)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_unb64(claims_seg))
    assert claims["iss"] == "12345"
    assert claims["iat"] == 1_000_000 - 60  # backdated for clock skew
    assert claims["exp"] == 1_000_000 + 540  # within GitHub's 10-minute cap

    public = serialization.load_pem_private_key(pem.encode(), password=None).public_key()
    # Raises InvalidSignature if the signature doesn't verify — so reaching the assert means it did.
    public.verify(  # type: ignore[call-arg]
        _unb64(sig_seg),
        f"{header_seg}.{claims_seg}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_app_jwt_rejects_a_non_rsa_key() -> None:
    ed = ed25519.Ed25519PrivateKey.generate()
    pem = ed.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    with pytest.raises(GitHubAccessError, match="RSA key"):
        _app_jwt("12345", pem, now=1_000_000)


def test_app_jwt_rejects_a_malformed_key_with_a_clean_error() -> None:
    # A non-PEM (or passphrase-protected) key surfaces a legible cause, not cryptography's raw error.
    with pytest.raises(GitHubAccessError, match="could not be loaded"):
        _app_jwt("12345", "-----BEGIN RSA PRIVATE KEY-----\nnope\n-----END RSA PRIVATE KEY-----\n")


def test_installation_token_resolves_installation_then_exchanges() -> None:
    calls: list[tuple[str, str]] = []

    def fake_fetch(url: str, jwt: str, method: str) -> bytes:
        calls.append((url, method))
        if url.endswith("/installation"):
            return json.dumps({"id": 999}).encode()
        return json.dumps({"token": "ghs_installationtoken"}).encode()

    tok = installation_token("12345", _rsa_pem(), _SPEC, fetch=fake_fetch)
    assert tok == "ghs_installationtoken"
    assert calls == [
        ("https://api.github.com/repos/acme/repo/installation", "GET"),
        ("https://api.github.com/app/installations/999/access_tokens", "POST"),
    ]


def test_installation_token_skips_the_lookup_when_pinned() -> None:
    calls: list[tuple[str, str]] = []

    def fake_fetch(url: str, jwt: str, method: str) -> bytes:
        calls.append((url, method))
        return json.dumps({"token": "ghs_pinned"}).encode()

    tok = installation_token("12345", _rsa_pem(), _SPEC, installation_id="42", fetch=fake_fetch)
    assert tok == "ghs_pinned"
    assert calls == [("https://api.github.com/app/installations/42/access_tokens", "POST")]


def test_installation_token_maps_a_malformed_body() -> None:
    # A 2xx body that isn't the expected JSON shape surfaces a legible GitHubAccessError rather than
    # a raw KeyError / JSONDecodeError (BE-0224 review fix).
    def bad_json(url: str, jwt: str, method: str) -> bytes:
        return b"<html>proxy error</html>"

    with pytest.raises(GitHubAccessError, match="not JSON"):
        installation_token("12345", _rsa_pem(), _SPEC, installation_id="42", fetch=bad_json)

    def missing_token(url: str, jwt: str, method: str) -> bytes:
        return json.dumps({"expires_at": "..."}).encode()  # no "token" field

    with pytest.raises(GitHubAccessError, match="'token'"):
        installation_token("12345", _rsa_pem(), _SPEC, installation_id="42", fetch=missing_token)


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "App JWT was rejected"), (404, "not installed"), (500, "returned 500")],
)
def test_fetch_maps_app_api_errors(monkeypatch, status: int, needle: str) -> None:  # type: ignore[no-untyped-def]
    import urllib.error
    from email.message import Message

    def raise_error(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, status, "err", Message(), None)

    monkeypatch.setattr("urllib.request.urlopen", raise_error)
    with pytest.raises(GitHubAccessError, match=needle):
        _fetch("https://api.github.com/app/installations/1/access_tokens", "jwt", "POST")
