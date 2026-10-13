"""Per-platform models for a target's platform-shaped groups, and the registry that holds them (BE-0452).

Not yet read by the config loader: the schema switch-over (BE-0452 unit 4) validates each target's
`app`, `runsOn`, `driver`, and `run` extension through `validate_group`.
"""

from .registry import (
    GROUPS,
    PLATFORM_SCHEMAS,
    Group,
    PlatformSchema,
    allowed_fields,
    schema_for,
    validate_group,
)

__all__ = [
    "GROUPS",
    "PLATFORM_SCHEMAS",
    "Group",
    "PlatformSchema",
    "allowed_fields",
    "schema_for",
    "validate_group",
]
