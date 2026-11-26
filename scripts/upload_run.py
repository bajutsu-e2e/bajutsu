#!/usr/bin/env python3
"""Upload one finished run to a `bajutsu serve` and print its report URL (BE-0510).

Standard library only, and nothing from `bajutsu`, so the file can be copied to any machine that
holds a run:

    python3 scripts/upload_run.py runs/<runId> --server https://bajutsu.example.com

The exit status says whether the import succeeded. It never reflects the run's verdict.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

KEY_VARIABLE = "BAJUTSU_SERVE_KEY"


class UploadError(Exception):
    """The upload did not happen; the message says why."""


def run_files(run_dir: Path) -> list[Path]:
    """The files to send. A symlink is left out, so nothing outside the run travels with it, and
    named on stderr. A directory that cannot be read stops the upload rather than shrinking it."""

    def stop(error: OSError) -> None:
        raise error

    files: list[Path] = []
    for directory, subdirectories, names in os.walk(run_dir, onerror=stop):
        for name in [*subdirectories, *names]:
            path = Path(directory) / name
            if path.is_symlink():
                print(f"skipping the symlink {path}", file=sys.stderr)
            elif name in names and path.is_file():
                files.append(path)
    return sorted(files)


def resolve_run(run: str, runs_dir: Path) -> Path:
    """*run* as a run directory: a path to one, or a run id under *runs_dir*."""
    path = Path(run)
    if not path.is_dir():
        path = runs_dir / run
    path = path.resolve()
    if not (path / "manifest.json").is_file():
        raise UploadError(f"no finished run at {path} (it has no manifest.json)")
    return path


def checked_server(server: str) -> str:
    """*server* without a trailing slash, refused when a key would cross the network in the clear."""
    parts = urlsplit(server)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise UploadError(f"not a server address: {server!r} (expected https://host[:port])")
    if parts.scheme == "http" and not _is_loopback(host):
        raise UploadError(f"refusing http:// to {host}: use https://, or http:// on loopback only")
    return server.rstrip("/")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def read_key(key_file: str | None) -> str | None:
    """The key from *key_file*, else the environment; never from an argument, which shell history
    keeps. None sends no credential, for a server that asks for none."""
    if key_file is None:
        key = os.environ.get(KEY_VARIABLE, "").strip()
        return _checked_key(key) if key else None
    try:
        key = Path(key_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        raise UploadError(f"cannot read the key file {key_file}") from e
    if not key:
        raise UploadError(f"the key file {key_file} is empty")
    return _checked_key(key)


def _checked_key(key: str) -> str:
    # Never echoed. Anything else cannot travel in a header.
    if not (key.isascii() and key.isprintable()):
        raise UploadError("the key must be one line of printable ASCII")
    return key


def write_zip(run_dir: Path, out: IO[bytes]) -> None:
    """Zip *run_dir* into *out*, under one top-level folder named for the run id."""
    # A file dated before 1980 is still sent; the zip format just cannot hold its date.
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in run_files(run_dir):
            zf.write(path, f"{run_dir.name}/{path.relative_to(run_dir).as_posix()}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect as a failure: urllib would follow it as a body-less GET, so nothing would
    be imported, and whatever answered that GET could pass for the import's reply."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:  # noqa: ARG002
        return None


def post_zip(server: str, body: IO[bytes], size: int, key: str | None) -> dict[str, object]:
    """Send the zip in *body* and return the server's reply."""
    request = urllib.request.Request(  # noqa: S310  `checked_server` allows http(s) alone
        f"{server}/api/runs/import",
        data=body,
        headers={"Content-Type": "application/zip", "Content-Length": str(size)},
        method="POST",
    )
    if key is not None:
        # Unredirected, so a redirect never carries the key to another host.
        request.add_unredirected_header("Authorization", f"Bearer {key}")
    # A proxy from the environment would carry a loopback http:// request, key and all, off the
    # machine.
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if server.startswith("http://"):
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(request) as response:
            content = response.read()
    except urllib.error.HTTPError as e:
        raise UploadError(f"the server answered {e.code}: {_error_text(e)}") from e
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        # A server refusing a body over its size limit closes the connection mid-send.
        raise UploadError(
            f"could not send the run: {getattr(e, 'reason', e)} (a server also cuts off a run "
            "over its size limit this way)"
        ) from e
    try:
        reply = json.loads(content)
    except ValueError as e:
        raise UploadError("the server's reply is not JSON; check the --server address") from e
    if not (
        isinstance(reply, dict)
        and isinstance(reply.get("url"), str)
        and isinstance(reply.get("archive"), str)
    ):
        raise UploadError("the server's reply names no run URL; check the --server address")
    return reply


def _error_text(error: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(error.read())["error"])
    except (OSError, ValueError, KeyError, TypeError):
        return str(error.reason)


def _parser() -> argparse.ArgumentParser:
    # No abbreviations: `--key SECRET` must not be read as `--key-file`.
    parser = argparse.ArgumentParser(
        description="Upload a finished run to a bajutsu serve and print its report URL.",
        allow_abbrev=False,
    )
    parser.add_argument("run", help="a run directory, or a run id under --runs-dir")
    parser.add_argument("--server", required=True, help="the server address, https://host[:port]")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="default: runs")
    parser.add_argument("--key-file", help=f"a file holding the key; default: ${KEY_VARIABLE}")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent, only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        server = checked_server(args.server)
        run_dir = resolve_run(args.run, args.runs_dir)
        key = read_key(args.key_file)
        if args.dry_run:
            credential = "with a key" if key else "without a key"
            print(f"would POST {server}/api/runs/import {credential}:")
            for path in run_files(run_dir):
                print(f"  {run_dir.name}/{path.relative_to(run_dir).as_posix()}")
            return 0
        # On disk rather than in memory: screenshots and videos make a run large.
        with tempfile.TemporaryFile() as archive:
            write_zip(run_dir, archive)
            size = archive.tell()
            archive.seek(0)
            reply = post_zip(server, archive, size, key)
    except (UploadError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    dropped = reply.get("dropped")
    if isinstance(dropped, list) and dropped:
        print(
            f"the server dropped {len(dropped)} file(s) of a type it does not serve:",
            file=sys.stderr,
        )
        for name in dropped:
            print(f"  {name}", file=sys.stderr)
    print(f"{server}{reply['url']}")
    print(f"{server}{reply['archive']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
