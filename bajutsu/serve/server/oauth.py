"""GitHub OAuth client for the hosted backend (BE-0015 7b-2).

The `OAuthClient` seam is the slice of the OAuth web flow the serve operations need — build the
authorize URL, and exchange a callback code for the GitHub identity (login + org + Team memberships)
— so a fake can drive the gate without contacting GitHub. `GitHubOAuthClient` is the real
implementation; authlib is lazy-imported inside the exchange, so this module imports no authlib and
the default path / import guard stay clean. The org/Team-based sign-in gate and role resolution
(BE-0313) and the CSRF-state check live in the (provider-neutral) operations, not here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

_AUTHORIZE = "https://github.com/login/oauth/authorize"
_EXCHANGE_URL = "https://github.com/login/oauth/access_token"  # the OAuth token-exchange endpoint
_USER = "https://api.github.com/user"
_ORGS = "https://api.github.com/user/orgs"
_TEAMS = "https://api.github.com/user/teams"
# `read:org` lets us read the user's org memberships (including private ones) to map them to a
# bajutsu org, and their Team memberships for the editor/admin role check (BE-0313); `read:user`
# covers the login itself (BE-0015 multi-tenancy).
_SCOPE = "read:user read:org"


@dataclass
class Identity:
    """Who logged in: the GitHub *login*, the *orgs* (GitHub org logins) they belong to, and the
    *teams* they are a direct member of. The org list maps the user to a bajutsu org (BE-0015); the
    team list (each `"<github-org>/<team-slug>"`) decides the editor/admin role (BE-0313). Both are
    empty when GitHub isn't consulted for them."""

    login: str
    orgs: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)


@runtime_checkable
class OAuthClient(Protocol):
    """The slice of the GitHub OAuth flow the serve operations drive (so a fake can stand in)."""

    def authorize_url(self, state: str) -> str:
        """The GitHub authorize URL to redirect the browser to, carrying the CSRF *state*."""

    def fetch_identity(self, code: str) -> Identity | None:
        """Exchange an authorization *code* for a token and return the GitHub identity (login + org +
        Team memberships), or None on a failed exchange."""


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

    def fetch_identity(self, code: str) -> Identity | None:
        from authlib.integrations.httpx_client import OAuth2Client

        # `with` closes the underlying httpx client, so connections/fds aren't leaked on a busy server.
        with OAuth2Client(
            self._client_id, self._client_secret, redirect_uri=self._redirect_uri
        ) as client:
            # GitHub returns form-encoded by default; ask for JSON so authlib parses the token.
            client.fetch_token(_EXCHANGE_URL, code=code, headers={"Accept": "application/json"})
            headers = {"Accept": "application/vnd.github+json"}
            user = client.get(_USER, headers=headers)
            if user.status_code != 200:
                return None
            login = user.json().get("login")
            if not login:
                return None
            return Identity(
                login=str(login),
                orgs=_fetch_orgs(client, headers),
                teams=_fetch_teams(client, headers),
            )


def _paginate(client: object, headers: dict[str, str], url: str) -> list[dict[str, object]]:
    """Every list item across a paginated GitHub collection starting at *url*, following the `Link`
    header. Any failure — a non-200, a parse error, or a non-list page — stops and returns what was
    gathered so far; the caller decides what an empty result means for its own failure direction."""
    items: list[dict[str, object]] = []
    next_url: str | None = url
    while next_url:
        resp = client.get(next_url, headers=headers)  # type: ignore[attr-defined]
        if resp.status_code != 200:
            break
        try:
            page = resp.json()
        except ValueError:
            break
        if not isinstance(page, list):
            break
        items.extend(o for o in page if isinstance(o, dict))
        # httpx parses the GitHub `Link` header into `.links`; follow `next` until it's gone.
        next_url = resp.links.get("next", {}).get("url")
    return items


def _fetch_orgs(client: object, headers: dict[str, str]) -> list[str]:
    """Every GitHub org the user belongs to, following pagination (so a user in >30 orgs isn't
    truncated). Org membership only maps the user to a bajutsu org, so any failure — a non-200, a
    parse error, an unexpected shape — yields no orgs (the user falls back to the default org)
    rather than failing the login."""
    return [
        str(o["login"])
        for o in _paginate(client, headers, f"{_ORGS}?per_page=100")
        if o.get("login")
    ]


def _fetch_teams(client: object, headers: dict[str, str]) -> list[str]:
    """Every GitHub Team the user is a *direct* member of, as `"<github-org>/<team-slug>"`, following
    pagination. Team membership grants the editor/admin role (BE-0313), so — the opposite failure
    direction from `_fetch_orgs` — any failure yields no teams, which leaves the user at viewer
    (fail closed) rather than granting write access on a lookup that didn't actually confirm it.
    `/user/teams` lists a child Team distinct from its parent, so a later exact-match check stays
    flat by construction: only the configured Team matches, never a nested one beneath it."""
    teams: list[str] = []
    for t in _paginate(client, headers, f"{_TEAMS}?per_page=100"):
        org = t.get("organization")
        slug = t.get("slug")
        if isinstance(org, dict) and org.get("login") and slug:
            teams.append(f"{org['login']}/{slug}")
    return teams
