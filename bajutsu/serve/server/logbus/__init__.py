"""A Redis-backed LogBus for the hosted backend (BE-0015 server phase, revived by BE-0449).

`InMemoryLogBus` buffers each job's lines in one process. `RedisLogBus` keeps the same `LogBus`
contract — a late subscriber replays the whole log, the stream ends once the job is closed — but
stores the lines in Redis so the replica a worker posts its lines to and the replica serving
`/events` can be different processes: a Redis **list** holds every line (so a late subscriber replays
it) plus a **done** flag, polled for the live tail. `QueueLogBus` (BE-0449) publishes the lines
workers post to it when ``BAJUTSU_REDIS_URL`` is set, so replicas share one live log.

The redis client is **injected** (the `RedisLike` slice below), so this module imports no redis —
it's safe to import and unit-test without the ``worker`` extra.
"""

from .redis_like import RedisLike
from .redis_log_bus import _DEFAULT_TTL as _DEFAULT_TTL
from .redis_log_bus import _DONE as _DONE
from .redis_log_bus import _LINES as _LINES
from .redis_log_bus import RedisLogBus

__all__ = ["RedisLike", "RedisLogBus"]
