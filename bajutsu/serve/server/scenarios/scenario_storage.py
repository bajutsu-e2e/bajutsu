"""The per-project scenario storage seam the control plane reads and writes through."""

from __future__ import annotations

from typing import Any, Protocol


class ScenarioStorage(Protocol):
    """Per-project scenario storage the control plane reads and writes (a DB / object store)."""

    # Whether `read` answers from the bound config's own tree, rather than from text a client saved
    # (BE-0501): a scenario's hook commands take the config's source only in the first case.
    config_tree: bool

    def has_app(self, app: str, *, session: str | None = None, org: str = "") -> bool:
        """Whether *app* is a target this deployment can serve scenarios for.

        *session* and *org* name the request asking (BE-0393 unit 2): the target set comes from the
        bound configuration, and which configuration that is depends on the session — a target only
        the asking session's config declares must not read as unknown.
        """

    def list(self, app: str, *, session: str | None = None, org: str = "") -> list[dict[str, Any]]:
        """Every scenario in *app*, summarized for the UI.

        *session* and *org* name the request asking (BE-0393 unit 2). A storage-only backend ignores
        them — its scenarios are addressed by name within the org's prefix — but one that reads from
        the bound configuration's own tree needs them to resolve the same tree the rest of the request
        does.
        """

    def read(
        self, app: str, ref: str | None, *, session: str | None = None, org: str = ""
    ) -> str | None:
        """The YAML text of scenario *ref* in *app*, or None if absent. *session* / *org* as in
        `list`."""

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        """Persist *text* as scenario *ref* in *app*, returning the saved ref, or None if rejected."""
