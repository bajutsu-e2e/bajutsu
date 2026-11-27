"""The `github-check` adapter: one check run per group on the commit a pull request tests (BE-0500).

Every call goes through an installation token of the GitHub App for the group's repository, which
the caller's machine session verified at registration. See the GitHub REST API's check runs:
https://docs.github.com/en/rest/checks/runs
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC
from typing import Any

from bajutsu.serve.server.db import GroupEventClaim

from .group_sink import GroupSink

_API = "https://api.github.com"
# GitHub caps a check run's `output.summary` at this many characters.
SUMMARY_LIMIT = 65535
# GitHub's status for a request it understood and refused, which a refused takeover answers.
_UNPROCESSABLE = 422
_TAKEOVER_OUTPUT = {"title": "Queued", "summary": "Waiting for a worker to run this group."}

# (method, url, installation token, JSON body or None) -> the parsed JSON response; raises
# `GitHubApiError` on a non-2xx answer. Injectable, so the adapter tests offline.
Request = Callable[[str, str, str, "dict[str, Any] | None"], Any]


class GitHubApiError(Exception):
    """GitHub answered a check-run call with a non-2xx status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API returned {status}: {message}")
        self.status = status


class GitHubCheckAdapter:
    """Deliver a group's events as calls on its one check run.

    Args:
        token_for: An installation token of the App for an `owner/repo`.
        request: The HTTP seam; the real GitHub API when omitted.
        app_id: The App's id, so the lookup never adopts or takes over another App's run.
    """

    def __init__(
        self,
        *,
        token_for: Callable[[str], str],
        request: Request | None = None,
        app_id: str | None = None,
    ) -> None:
        self._token_for = token_for
        self._request = request or _request
        self._app_id = app_id

    def __call__(self, claim: GroupEventClaim, sink: GroupSink) -> str | None:
        """Deliver *claim*; returns the check run id for later events, or None to keep the given one."""
        repository = str(claim.notify["repository"])
        token = self._token_for(repository)
        call = _Calls(self._request, token, repository, self._app_id)
        fields = _event_fields(claim)
        if claim.event == "queued":
            return self._queue(call, claim, sink)
        if claim.reference:
            # A later group may have taken the run over since this event happened; writing to it
            # would show this group's state on that group's check.
            if call.get(claim.reference).get("external_id") == claim.group_id:
                call.patch(claim.reference, fields)
            return None
        runs = call.list(claim.head_sha(), sink.check_name_for(claim.notify["target"]))
        own = next(
            (
                r
                for r in runs
                if r.get("external_id") == claim.group_id and r["status"] != "completed"
            ),
            None,
        )
        if own is not None:
            call.patch(str(own["id"]), fields)
            return str(own["id"])
        return call.create(_new_run(claim, sink) | fields)

    @staticmethod
    def _queue(call: _Calls, claim: GroupEventClaim, sink: GroupSink) -> str:
        runs = call.list(claim.head_sha(), sink.check_name_for(claim.notify["target"]))
        mine = [r for r in runs if r.get("external_id") == claim.group_id]
        adopted = next((r for r in mine if r["status"] == "queued"), None)
        if adopted is not None:
            return str(adopted["id"])
        finished = [r for r in runs if r["status"] == "completed"]
        # A run carrying this group's id but still completed is a refused takeover's remnant: taking
        # another would only repeat the refusal, so a fresh run is created instead.
        if not mine and finished:
            newest = max(finished, key=lambda r: int(r["id"]))
            taken = call.takeover(
                str(newest["id"]),
                {
                    "status": "queued",
                    "external_id": claim.group_id,
                    "conclusion": None,
                    "completed_at": None,
                    "output": _TAKEOVER_OUTPUT,
                },
            )
            if taken is not None:
                return taken
        return call.create(_new_run(claim, sink) | {"status": "queued"})


class _Calls:
    """The check-run calls on one repository, under one token."""

    def __init__(self, request: Request, token: str, repository: str, app_id: str | None) -> None:
        self._request = request
        self._token = token
        self._base = f"{_API}/repos/{repository}"
        self._app_id = app_id

    def list(self, head_sha: str, name: str) -> list[dict[str, Any]]:
        # `filter=all`: the default returns only each name's latest run, hiding an older one.
        params: dict[str, object] = {"check_name": name, "filter": "all", "per_page": 100}
        if self._app_id:
            params["app_id"] = self._app_id
        # ponytail: one page of 100 runs; a commit re-run past that would need paging.
        query = urllib.parse.urlencode(params)
        body = self._request(
            "GET", f"{self._base}/commits/{head_sha}/check-runs?{query}", self._token, None
        )
        return list(body.get("check_runs") or [])

    def get(self, run_id: str) -> dict[str, Any]:
        answer: dict[str, Any] = self._request(
            "GET", f"{self._base}/check-runs/{run_id}", self._token, None
        )
        return answer

    def create(self, fields: dict[str, Any]) -> str:
        return str(self._request("POST", f"{self._base}/check-runs", self._token, fields)["id"])

    def patch(self, run_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = self._request(
            "PATCH", f"{self._base}/check-runs/{run_id}", self._token, fields
        )
        return answer

    def takeover(self, run_id: str, fields: dict[str, Any]) -> str | None:
        """Set a finished run back to queued, or None when GitHub refuses the return."""
        # GitHub does not document whether a completed run accepts `queued` again, so a refusal,
        # or an answer still not queued, falls back to a new run. Any other error (a rate limit, a
        # lost permission, an outage) retries the delivery, since a new run would duplicate the check.
        try:
            answer = self.patch(run_id, fields)
        except GitHubApiError as e:
            if e.status == _UNPROCESSABLE:
                return None
            raise
        return run_id if answer.get("status") == "queued" else None


def _new_run(claim: GroupEventClaim, sink: GroupSink) -> dict[str, Any]:
    return {
        "name": sink.check_name_for(claim.notify["target"]),
        "head_sha": claim.head_sha(),
        "external_id": claim.group_id,
    }


def _event_fields(claim: GroupEventClaim) -> dict[str, Any]:
    at = claim.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if claim.event == "started":
        return {"status": "in_progress", "started_at": at}
    if claim.event == "completed":
        snapshot = claim.snapshot or {}
        return {
            "status": "completed",
            "conclusion": snapshot.get("conclusion"),
            "completed_at": at,
            "output": check_output(snapshot),
        }
    return {"status": "queued"}


def check_output(snapshot: dict[str, Any]) -> dict[str, str]:
    """The check run's `output`: the counts as its title, a table of what did not pass as its summary.

    Every cell is escaped, and rows stop before GitHub's summary cap, ending with how many were left
    out. No annotations: a scenario has no file and line in the pull request's diff.
    """
    total, passed, rows = (
        snapshot.get("total", 0),
        snapshot.get("passed", 0),
        snapshot.get("rows", []),
    )
    title = (
        f"{len(rows)} of {total} scenarios did not pass"
        if rows
        else f"{passed} of {total} scenarios passed"
    )
    footer = f"\n\n{passed} scenarios passed."
    if not rows:
        return {"title": title, "summary": footer.strip()}
    lines = ["| Scenario | Outcome | Reason |", "|---|---|---|"]
    used = sum(len(line) + 1 for line in lines) + len(footer)
    for index, row in enumerate(rows):
        line = f"| {_cell(row['scenario'])} | {_cell(row['outcome'])} | {_cell(row['reason'])} |"
        trailer = f"| … | {len(rows) - index} more rows not shown | |"
        if used + len(line) + 1 + len(trailer) + 1 > SUMMARY_LIMIT:
            lines.append(trailer)
            break
        lines.append(line)
        used += len(line) + 1
    return {"title": title, "summary": "\n".join(lines) + footer}


def _cell(text: object) -> str:
    """*text* as one inert table cell: no control characters, `|` escaped, inside a code span."""
    clean = "".join(c for c in str(text) if unicodedata.category(c) != "Cc").replace("|", "\\|")
    if not clean:
        return ""
    fence = "`" * (max((len(run) for run in re.findall(r"`+", clean)), default=0) + 1)
    return f"{fence} {clean} {fence}"


def _request(method: str, url: str, token: str, body: dict[str, Any] | None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — an https GitHub API URL built above
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        # GitHub explains a refusal in the body, which the operator's log needs.
        detail = e.read(2048).decode("utf-8", "replace")
        raise GitHubApiError(e.code, f"{e.reason}: {detail}") from e
