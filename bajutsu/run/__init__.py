"""`bajutsu run`: target/backend resolution, device leasing, plan construction, and the
deterministic run/report dispatch (`cli.py`). No package-level re-export — the one caller is
`bajutsu.cli`, which mounts `cli.register` directly.

`INTERNAL_MODULE` names the internal entry point (`internal.py`) the run body executes behind
(BE-0449). It lives here, import-free, so the serve command builders and the job-kind derivation
can name it without importing the run body.
"""

from __future__ import annotations

# The module a job spec spawns as `python -m <this>` to execute the body of `bajutsu run`. The
# public `bajutsu run` is a wrapper that spawns it too, so a spawned run never re-enters the wrapper.
INTERNAL_MODULE = "bajutsu.run.internal"

# The environment variable naming the read end of a pipe whose write end the one-shot caller holds
# open for as long as it waits on the run. The run treats end of file on it as the caller's death.
PARENT_FD_ENV = "BAJUTSU_PARENT_FD"
