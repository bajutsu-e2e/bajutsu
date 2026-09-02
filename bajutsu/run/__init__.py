"""`bajutsu run`: target/backend resolution, device leasing, plan construction, and the
deterministic run/report dispatch (`cli.py`). No package-level re-export — the one caller is
`bajutsu.cli`, which mounts `cli.register` directly.
"""

from __future__ import annotations
