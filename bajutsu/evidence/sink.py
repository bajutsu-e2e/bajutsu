"""The single write path into a run directory (BE-0331).

Redaction used to be a habit: the `Redactor` existed, the evidence writers called it, and every
other writer was on its honor to do the same. This module makes it a property of the location
instead. A caller hands the sink a *relative name* and content, never a path; the sink resolves the
name against the run directory, redacts, serializes, and writes. Withholding the path is what makes
the boundary enforceable — a module that never receives the run directory cannot write into it
without the sink.

Content arrives *before* serialization, and there is one entry point per shape, because two of the
default rules are structural rather than textual: the masked-input trait keys on an element's trait,
and the credential-named default on an element's identifier or label. Once an element tree is a JSON
string that pairing is gone, so a sink that only scanned serialized text could apply neither, and
routing the network writer through such a sink would silently drop the header masking BE-0130 made
default-on. The pattern backstop runs last, over the serialized text, so it still catches a value
the structural rules did not reach.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bajutsu.artifact_perms import restrict_file
from bajutsu.drivers import base
from bajutsu.evidence.redaction import Redactor, mask_credential_shapes

# Aliased private: imported under its public name, this module would re-export the very path
# derivation the import contract withholds, so `from bajutsu.evidence.sink import run_dir_for_write`
# would hand a caller a writable run directory while still passing `lint-imports`.
from bajutsu.run_root import run_dir_for_write as _run_dir_for_write

_logger = logging.getLogger(__name__)


def prepare_run_dir(runs_dir: str | Path, run_id: str) -> None:
    """Create a run directory owner-only before a *subdirectory* materializes it (BE-0131).

    A sink creates its own directory, so a run whose first write goes through one needs nothing here.
    The cross-browser matrix is the exception: each engine pass writes under `run_dir/<engine>`, and
    the sink creating that directory would materialize the run directory above it with
    `mkdir(parents=True)`, at the ambient umask. Returns nothing, so the boundary is unchanged — a
    caller gets no writable handle from it.
    """
    _run_dir_for_write(runs_dir, run_id)


class RunArtifactWriter:
    """Writes every artifact of one run, applying the run's redactor on the way in.

    Construct one per run directory and pass it wherever a writer used to receive the directory
    itself. `unmasked` names the artifacts written without inspection (images, video, archives), so
    an artifact is never described as scrubbed by rules that did not run — the honesty BE-0151
    established for screenshots, kept for every opaque shape.
    """

    def __init__(self, run_dir: Path, redactor: Redactor) -> None:
        # The write provider is reached from here and nowhere else (BE-0331 unit 3): the sink is the
        # single module the import contract lets derive a writable run directory.
        self._dir = _run_dir_for_write(run_dir.parent, run_dir.name)
        self._redactor = redactor
        self.unmasked: list[str] = []

    @property
    def redactor(self) -> Redactor:
        """The run's redactor, for a caller that must scrub content it streams itself."""
        return self._redactor

    def write_elements(self, name: str, elements: list[base.Element]) -> Path:
        """Write an element tree, masking each element's value structurally first."""
        return self._write_json(name, self._redactor.redact_elements(elements))

    def write_exchanges(self, name: str, exchanges: list[dict[str, Any]]) -> Path:
        """Write captured network exchanges, masking sensitive headers, urls and bodies.

        The exchange entry point exists so BE-0130's default header masking keeps running inside the
        boundary rather than being left behind in the caller.
        """
        return self._write_json(name, [self._redactor.redact_exchange(e) for e in exchanges])

    def write_screen_map(self, name: str, screen_map: dict[str, Any]) -> Path:
        """Write a crawl's screen map, masking the input values its actions carry.

        The structural rule reaches an action's `value`/`fields` only; a node's ids, an edge's
        description, a stop reason and a crash path carry free text an app can echo a secret into,
        so the key/known-value pass runs over the map's own keys and strings as well.
        """
        return self._write_json(name, self._redactor.redact_screen_map(screen_map), scrub_text=True)

    def write_json(self, name: str, data: Any) -> Path:
        """Write structured content with no shape-specific rule — the key/known-value path."""
        return self._write_json(name, data, scrub_text=True)

    def write_text(self, name: str, text: str) -> Path:
        """Write free text (a log, a report, a rendered scenario), scrubbed as free text."""
        return self._write(name, self._scrub(name, self._redactor.redact_text(text)))

    def write_bytes(self, name: str, data: bytes) -> Path:
        """Write content the sink cannot inspect — an image, a video, an archive.

        Recorded in `unmasked`: an image is not claimed to be protected.
        """
        self.unmasked.append(name)
        return self._replace(name, lambda tmp: tmp.write_bytes(data))

    def reserve(self, name: str) -> Path:
        """A path for content an external recorder writes itself (simctl, screenrecord, logcat).

        The one place the sink hands out a path, because a subprocess cannot be handed bytes. It is
        not a hole in the boundary: nothing is redacted *at* reservation, and the caller must close
        the loop with `scrub_reserved` for text or `record_unmasked` for an opaque recording, so the
        artifact still crosses redaction before it ships.
        """
        path = self._resolve(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def record_unmasked(self, name: str) -> None:
        """Note that a reserved artifact holds content the sink could not inspect.

        Restricts the file like every other artifact the sink writes: a reserved recording is
        exactly the kind that carries on-screen secrets (a screenshot, a video), and the recorder
        that wrote it obeyed the ambient umask (BE-0131).
        """
        self.unmasked.append(name)
        restrict_file(self._resolve(name))

    def scrub_reserved(self, name: str) -> bool:
        """Scrub a reserved text artifact in place; return whether it is safe to ship.

        Safe (True) means there is nothing left to leak: it was scrubbed, or there was nothing to
        read. Unsafe (False) means the file exists but could not be read, so the caller must not
        emit it — redaction is a security control, so it fails closed rather than shipping unread
        bytes.
        """
        path = self._resolve(name)
        if not path.exists():
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        scrubbed = self._scrub(name, self._redactor.redact_text(text))
        if scrubbed != text:
            path.write_text(scrubbed, encoding="utf-8")
        restrict_file(path)
        return True

    def _write_json(self, name: str, data: Any, *, scrub_text: bool = False) -> Path:
        # A shape-specific rule already ran over the structure; `scrub_text` additionally runs the
        # key/known-value pass for content that had no such rule. It runs in two halves, on either
        # side of serialization, because a key pattern reaches to end of line: shown a serialized
        # document it consumes a string's closing quote and the artifact stops parsing — which the
        # resume path and the web UI's live poll both read back (BE-0331).
        if scrub_text:
            data = self._redactor.redact_structure(data)
        # `ensure_ascii=False`: a run's evidence is read by people, and an app under test in
        # Japanese would otherwise land as a wall of \uXXXX escapes.
        body = json.dumps(data, ensure_ascii=False, indent=2)
        if scrub_text:
            body = self._redactor.redact_values(body)
        return self._write(name, self._scrub(name, body))

    def _scrub(self, name: str, text: str) -> str:
        """The pattern backstop, run last over serialized text (BE-0331 unit 7)."""
        masked, shapes = mask_credential_shapes(text)
        if shapes:
            # An earlier, more precise rule should have caught this, so the match is worth naming
            # rather than masking silently.
            _logger.warning(
                "masked a recognizable credential shape (%s) in %s — an earlier redaction rule "
                "should have covered it",
                ", ".join(shapes),
                name,
            )
        return masked

    def _write(self, name: str, text: str) -> Path:
        return self._replace(name, lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def _replace(self, name: str, emit: Callable[[Path], object]) -> Path:
        """Write through a sibling temp file, then rename onto the artifact.

        Every artifact is written atomically, not just the ones whose callers thought to: a reader
        polling a run directory while it is being written (the web UI following a live crawl's screen
        map) never sees a half-written file. The temp file is restricted before the rename, so the
        artifact is never briefly world-readable under a permissive umask (BE-0131).
        """
        path = self._resolve(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        emit(tmp)
        restrict_file(tmp)
        tmp.replace(path)
        return path

    def _resolve(self, name: str) -> Path:
        """Resolve a relative artifact name against the run directory, refusing to escape it."""
        root = self._dir.resolve()
        path = (root / name).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"artifact name escapes the run directory: {name!r}")
        return path
