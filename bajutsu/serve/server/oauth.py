"""GitHub OAuth client for the hosted backend (BE-0015 7b-2).

The `OAuthClient` seam is the slice of the OAuth web flow the serve operations need — build the
authorize URL, and exchange a callback code for the GitHub login — so a fake can drive the gate
without contacting GitHub. `GitHubOAuthClient` is the real implementation; authlib is lazy-imported
inside the exchange, so this module imports no authlib and the default path / import guard stay
clean. The username allowlist and CSRF-state check live in the (provider-neutral) operations, not
here."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

_AUTHORIZE = "https://github.com/login/oauth/authorize"
_TOKEN = "https://github.com/login/oauth/access_token"  # the OAuth token-exchange endpoint
_USER = "https://api.github.com/user"
_SCOPE = "read:user"  # only the login is needed for the allowlist check


@runtime_checkable
class OAuthClient(Protocol):
    """The slice of the GitHub OAuth flow the serve operations drive (so a fake can stand in)."""

    def authorize_url(self, state: str) -> str:
        """The GitHub authorize URL to redirect the browser to, carrying the CSRF *state*."""

    def fetch_login(self, code: str) -> str | None:
        """Exchange an authorization *code* for a token and return the GitHub login, or None."""


class GitHubOAuthClient:
    """Real GitHub OAuth client. Holds only config at construction (no network, no authlib); authlib
    is imported when a code is exchanged."""

    def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": _SCOPE,
                "state": state,
            }
        )
        return f"{_AUTHORIZE}?{query}"

    def fetch_login(self, code: str) -> str | None:
        from authlib.integrations.httpx_client import OAuth2Client

        client = OAuth2Client(self._client_id, self._client_secret, redirect_uri=self._redirect_uri)
        # GitHub returns form-encoded by default; ask for JSON so authlib parses the token.
        client.fetch_token(_TOKEN, code=code, headers={"Accept": "application/json"})
        resp = client.get(_USER, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return None
        login = resp.json().get("login")
        return str(login) if login else None
