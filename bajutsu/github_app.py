"""Mint a GitHub App installation access token for the private-repo config source (BE-0224).

A GitHub App is the service-identity answer for an unattended, possibly multi-tenant self-hosted
`serve` ([BE-0016]): the token is short-lived, limited to the installation's repos, and tied to the
service rather than a person. The flow is the standard two steps — sign a short-lived RS256 JSON Web
Token (JWT) with the App private key, then exchange it for an installation access token.

Optional by construction: this module (and, through it, `cryptography`) is imported only when App
credentials are configured (`config_source._github_app_credential`), so a deployment that uses a
personal access token pulls in nothing extra. The RS256 signature uses `cryptography` directly (no
separate JWT library); its absence is reported with the extra to install, not an `ImportError`.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING

from bajutsu.config_source import GitHubAccessError

if TYPE_CHECKING:
    from bajutsu.config_source import GitConfigSpec

_API = "https://api.github.com"
# GitHub caps an App JWT's lifetime at 10 minutes; backdate `iat` 60s to tolerate clock skew.
_JWT_TTL_SECONDS = 540
_CLOCK_SKEW_SECONDS = 60

# An injectable "GET/POST this GitHub API URL with the App JWT, return the JSON body" seam, so the
# token flow tests offline against a fake — the one external dependency, mirroring `Transport`.
Fetch = Callable[[str, str, str], bytes]


def installation_token(
    app_id: str,
    private_key_pem: str,
    spec: GitConfigSpec,
    *,
    installation_id: str | None = None,
    fetch: Fetch | None = None,
) -> str:
    """A short-lived installation access token for `spec`'s repo (BE-0224).

    Resolves the installation from the repo when *installation_id* is not pinned, then exchanges the
    App JWT for the installation token. *fetch* is injectable so the flow tests offline.
    """
    jwt = _app_jwt(app_id, private_key_pem)
    get = fetch if fetch is not None else _fetch
    if not installation_id:
        body = get(f"{_API}/repos/{spec.owner}/{spec.repo}/installation", jwt, "GET")
        installation_id = str(_json_field(body, "id"))
    body = get(f"{_API}/app/installations/{installation_id}/access_tokens", jwt, "POST")
    return str(_json_field(body, "token"))


def _json_field(body: bytes, field: str) -> object:
    """Read *field* from a GitHub JSON response, mapping a malformed body or a missing field to a
    legible `GitHubAccessError` rather than a raw `JSONDecodeError` / `KeyError` (BE-0224).

    The two failures are separated so a non-JSON body (an HTML error page from a proxy) is named as
    such, not misreported as a missing field.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise GitHubAccessError(f"unexpected GitHub App API response (not JSON): {e}") from e
    try:
        return parsed[field]
    except (KeyError, TypeError) as e:
        raise GitHubAccessError(
            f"unexpected GitHub App API response (no {field!r} field): {e}"
        ) from e


def _app_jwt(app_id: str, private_key_pem: str, *, now: int | None = None) -> str:
    """A signed RS256 App JWT (`iss` = app id, short `exp`), the credential for the App-level API.

    *now* is injectable so the claims are asserted deterministically in tests; it defaults to the
    current time.
    """
    if now is None:
        now = int(time.time())
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ModuleNotFoundError as e:
        raise GitHubAccessError(
            "GitHub App authentication needs the 'cryptography' package: install the githubapp extra "
            "(`uv sync --extra githubapp`), or authenticate with GITHUB_TOKEN instead."
        ) from e
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as e:
        # A malformed PEM raises ValueError; a passphrase-protected key raises TypeError ("Password
        # was not given"). Name the fix rather than letting cryptography's raw error escape.
        raise GitHubAccessError(
            f"the GitHub App private key could not be loaded (it must be an unencrypted PEM): {e}"
        ) from e
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubAccessError("the GitHub App private key must be an RSA key")
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    claims = _b64url(
        json.dumps(
            {"iat": now - _CLOCK_SKEW_SECONDS, "exp": now + _JWT_TTL_SECONDS, "iss": app_id}
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(signature)}"


def _b64url(data: bytes) -> str:
    """base64url without padding — the JWT segment encoding (RFC 7515)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _fetch(url: str, jwt: str, method: str) -> bytes:
    """The real App-API call: the App JWT as Bearer, mapping an HTTP error to a legible cause."""
    req = urllib.request.Request(  # noqa: S310 — https GitHub API URL
        url,
        method=method,
        headers={"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return bytes(resp.read())
    except urllib.error.HTTPError as e:
        # 401 here rejects the App JWT itself (a wrong App id, or a key that isn't this App's); 404
        # on the installation lookup means the App isn't installed on the repo. Name these directly
        # rather than blaming repo access, which an App misconfiguration is not.
        if e.code == 401:
            cause = (
                "the App JWT was rejected — check BAJUTSU_GITHUB_APP_ID and the private key match"
            )
        elif e.code == 404:
            cause = "the App is not installed on this repository, or the installation id is wrong"
        else:
            cause = f"the GitHub App API returned {e.code}"
        raise GitHubAccessError(f"GitHub App authentication failed: {cause}.") from e
