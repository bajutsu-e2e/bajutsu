"""The slice of a redis-py client the Redis log bus uses, so a fake can stand in."""

from __future__ import annotations

from typing import Protocol


class RedisLike(Protocol):
    """The slice of a redis-py client `RedisLogBus` uses (so a fake can stand in)."""

    def rpush(self, key: str, *values: str) -> object:
        """Append *values* to the list at *key*, in one command."""

    def lrange(self, key: str, start: int, end: int) -> list[object]:
        """Return the list at *key* from index *start* to *end* (`-1` = last)."""

    def set(self, key: str, value: str) -> object:
        """Set *key* to *value*."""

    def get(self, key: str) -> object:
        """Return *key*'s value, or None if unset."""

    def expire(self, key: str, seconds: int) -> object:
        """Set *key* to expire in *seconds* (bounding a finished job's log lifetime)."""
