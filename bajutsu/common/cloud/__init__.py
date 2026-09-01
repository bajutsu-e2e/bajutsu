"""Cloud device backends reached as batch submitters, off the deterministic `run`/CI verdict path.

A cloud backend here ferries a deterministic Bajutsu run to a remote host that already holds a
reserved device and back; the verdict still comes from Bajutsu's own ``manifest.json``, never from
the cloud's own run classification (prime directive 1). `devicefarm` is the first concrete provider
(AWS Device Farm); the ``aws`` extra it needs is optional and imported lazily behind a Protocol seam,
so this package imports without it.
"""

from __future__ import annotations
