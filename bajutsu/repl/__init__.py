"""The repl feature (BE-0423): a manual shell over the shared `Driver`, with no AI anywhere.

`render.py` prints an element tree; `session.py` is the command set (one method per verb, each a
thin call on the driver); `loop.py` is the `bajutsu>` prompt loop; `cli.py` is the `bajutsu repl`
command wired onto them. No package-level re-export — every caller already names a specific module
(`bajutsu.repl.session`, …), as `bajutsu.record` does.
"""

from __future__ import annotations
