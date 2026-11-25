"""Scenario storage over S3-compatible object storage."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from bajutsu.serve.helpers import summarize_scenario, valid_scenario_ref
from bajutsu.serve.server.object_store import ObjectStore, scenario_prefix


class ObjectScenarioStorage:
    """`ScenarioStorage` backed by S3-compatible object storage (the roadmap's R2).

    Scenarios live at ``<prefix>scenarios/<app>/<name>.yaml`` in one bucket; *prefix* is prepended
    so a tenant prefix (``<org>/``) can scope a shared bucket later — multi-tenant slots in without
    a contract change. The set of known projects comes from *apps* (the control plane's configured
    apps), keeping a Postgres registry out of the single-tenant path. The object-store client is
    injected (the `ObjectStore` slice), so a fake drives the gate."""

    config_tree = False

    def __init__(
        self,
        store: ObjectStore,
        apps: Callable[[str | None, str], Collection[str]],
        *,
        prefix: str = "",
    ) -> None:
        self._store = store
        self._apps = apps
        self._prefix = prefix

    def _dir(self, app: str) -> str:
        return f"{scenario_prefix(self._prefix)}{app}/"

    def has_app(self, app: str, *, session: str | None = None, org: str = "") -> bool:
        return app in self._apps(session, org)

    def list(
        self,
        app: str,
        *,
        session: str | None = None,  # noqa: ARG002  # ScenarioStorage shape
        org: str = "",  # noqa: ARG002  # ScenarioStorage shape
    ) -> list[dict[str, Any]]:
        # Addressed by name within the org's own object-store prefix, never by a path resolved from
        # the bound configuration, so the asking session decides nothing here (BE-0393 unit 2).
        base = self._dir(app)
        out: list[dict[str, Any]] = []
        for key in sorted(self._store.list_keys(base)):
            name = key[len(base) :]
            # Only direct children that read/save would accept, so list never shows an entry that
            # can't then be read or run. valid_scenario_ref enforces a safe *.yaml ref.
            if "/" in name or not valid_scenario_ref(name):
                continue
            data = self._store.get_bytes(key)
            # Decode leniently: a non-UTF-8 object degrades to a bare entry, never 500s the listing.
            text = data.decode("utf-8", errors="replace") if data else ""
            out.append(summarize_scenario(name, name, text))
        return out

    def read(
        self,
        app: str,
        ref: str | None,
        *,
        session: str | None = None,  # noqa: ARG002  # ScenarioStorage shape
        org: str = "",  # noqa: ARG002  # ScenarioStorage shape
    ) -> str | None:
        # Keyed by name under the org's prefix, so the asking session decides nothing (see `list`).
        if not ref:
            return None
        data = self._store.get_bytes(f"{self._dir(app)}{ref}")
        # Lenient decode: user-authored text shouldn't 500 the UI if it isn't valid UTF-8.
        return data.decode("utf-8", errors="replace") if data is not None else None

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        if not ref:
            return None
        self._store.put_bytes(f"{self._dir(app)}{ref}", text.encode("utf-8"))
        return ref
