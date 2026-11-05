"""The top-level key segments of the bucket layout, shared by the store and the org-slug check.

The default org keeps its objects at the bucket root (``artifacts/…``, ``baselines/…``), and every
other org nests the same layout under ``<slug>/``. An org whose slug equalled one of these segments
would therefore write inside the default org's tree, and on a local start into the runs tree or the
``--baselines`` directory the store mounts there (BE-0449). The store builds its prefixes from these
names and the org checks refuse them as slugs, so one list governs both. Dependency-free, so the
default `serve` path can import it.
"""

from __future__ import annotations

ARTIFACTS = "artifacts"
SCENARIOS = "scenarios"
BASELINES = "baselines"
UPLOADS = "uploads"

# Every segment the default org's layout uses at the bucket root.
DEFAULT_ORG_SEGMENTS: frozenset[str] = frozenset({ARTIFACTS, SCENARIOS, BASELINES, UPLOADS})


def reserved_slug_error(slug: str) -> str | None:
    """Why *slug* cannot name an org because the bucket layout already uses it, or None."""
    if slug in DEFAULT_ORG_SEGMENTS:
        reserved = ", ".join(sorted(DEFAULT_ORG_SEGMENTS))
        return (
            f"org slug {slug!r} is reserved: the object store keeps the default org's "
            f"{reserved} folders under these names"
        )
    return None
