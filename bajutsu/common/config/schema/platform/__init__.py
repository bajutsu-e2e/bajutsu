"""Per-platform models for a target's platform-shaped groups, and the registry that holds them (BE-0452).

`TargetConfig` validates each target's `subject`, `runsOn`, and `driver` through these models after merging `defaults` into the target.
"""

from .registry import (
    GROUPS,
    PLATFORM_SCHEMAS,
    Group,
    PlatformSchema,
    accepts_field,
    allowed_fields,
    schema_for,
    validate_group,
    validate_model,
)

__all__ = [
    "GROUPS",
    "PLATFORM_SCHEMAS",
    "Group",
    "PlatformSchema",
    "accepts_field",
    "allowed_fields",
    "schema_for",
    "validate_group",
    "validate_model",
]
