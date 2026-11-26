"""Borrow one device from a lease broker by running its lease command (BE-0511).

The repository names no broker; the contract is the command's behavior. It stays running while the
device is held, prints one line naming the device within the acquire timeout, and ends the lease
when it is terminated or its standard input reaches end of file. The `leased` device provider and
the worker both borrow through here, so there is one reading of that contract.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO

from bajutsu.common.capability.worker_capability import DEVICE_PLACEHOLDER, PLATFORM_PLACEHOLDER
from bajutsu.common.config import Effective

# The most of the answer line, and of the broker's standard error, that is ever held.
_MAX_ANSWER_BYTES = 65536
_STDERR_TAIL_BYTES = 4096

_logger = logging.getLogger(__name__)


class LeaseDeclinedError(ValueError):
    """The broker lent no device: its command exited, or stayed silent, before naming one."""


@dataclass(frozen=True)
class BorrowedDevice:
    """One device the broker lent.

    Attributes:
        address: What the run drives, in place of a udid.
        env: The variables the run's device tools must see.
        release: Ends the lease; safe to call more than once.
        problem: Why the lease no longer holds, when its command exited before release; else None.
    """

    address: str
    release: Callable[[], None]
    env: Mapping[str, str] = field(default_factory=dict)
    problem: Callable[[], str | None] = lambda: None


def declared_models(eff: Effective) -> list[str]:
    """The device models *eff*'s `runsOn` names: `ios.model` or `android.avd`, none when unset."""
    key = {"ios": "model", "android": "avd"}.get(eff.platform)
    value = eff.conditions.get(key) if key is not None else None
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def lease_argv(command: Sequence[str], platform: str, device: str | None) -> list[str]:
    """*command* with its placeholders filled; no device drops `{device}` and the flag before it.

    Raises:
        ValueError: *device* begins with `-`, so the broker would read it as an option.
    """
    if device is not None and device.startswith("-"):
        raise ValueError(
            f"device model {device!r} begins with '-', so the lease command would read it as an "
            "option; name the device in `runsOn` without it"
        )
    argv: list[str] = []
    for element in command:
        if element != DEVICE_PLACEHOLDER:
            argv.append(element.replace(PLATFORM_PLACEHOLDER, platform))
        elif device is None:
            argv.pop()
        else:
            argv.append(device)
    return argv


def borrow_device(
    command: Sequence[str],
    *,
    platform: str,
    device: str | None,
    acquire_timeout: float,
    release_timeout: float,
) -> BorrowedDevice:
    """Run the lease command and wait up to *acquire_timeout* for the device it names.

    Raises:
        LeaseDeclinedError: The command could not start, exited, or printed no line in time.
        ValueError: The answer is not the contract's one data object, or *device* is flag-shaped.
    """
    argv = lease_argv(command, platform, device)
    try:
        # A session of its own, so a Ctrl-C at the worker's terminal does not end the lease while
        # the run it serves is still winding down. Standard input stays open until release, and the
        # operating system closes it if this process dies.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        raise LeaseDeclinedError(f"cannot start the lease command {argv[0]!r}: {e}") from e
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    answered = threading.Event()
    line: list[bytes] = []
    stderr_tail = bytearray()
    threading.Thread(
        target=_read_answer, args=(proc.stdout, line, answered), daemon=True, name="lease-stdout"
    ).start()
    errors = threading.Thread(
        target=_keep_tail, args=(proc.stderr, stderr_tail), daemon=True, name="lease-stderr"
    )
    errors.start()
    released = threading.Event()
    release = _releaser(proc, release_timeout, released)

    try:
        if not answered.wait(acquire_timeout):
            raise _silent(acquire_timeout, stderr_tail)  # noqa: TRY301  # released below
        if not line or not line[0].endswith(b"\n"):
            raise _ended_early(proc, errors, stderr_tail, release_timeout)  # noqa: TRY301  # same
        address, env = _parse_answer(line[0])
    except BaseException:
        release()
        raise

    def problem() -> str | None:
        if released.is_set() or proc.poll() is None:
            return None
        said = stderr_tail.decode(errors="replace").strip()
        return (
            f"the lease on {address} ended before the run released it "
            f"(exit status {proc.returncode})" + (f": {said}" if said else "")
        )

    return BorrowedDevice(address=address, env=env, release=release, problem=problem)


def _silent(acquire_timeout: float, stderr_tail: bytearray) -> LeaseDeclinedError:
    said = stderr_tail.decode(errors="replace").strip()
    return LeaseDeclinedError(
        f"the lease command printed no line within {acquire_timeout:g} seconds"
        + (f": {said}" if said else "")
    )


def _ended_early(
    proc: subprocess.Popen[bytes],
    errors: threading.Thread,
    stderr_tail: bytearray,
    release_timeout: float,
) -> LeaseDeclinedError:
    """The decline of a command that closed its output before naming a device, with its reason."""
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=release_timeout)
    errors.join(timeout=1.0)
    said = stderr_tail.decode(errors="replace").strip()
    status = proc.returncode
    ended = f"exit status {status}" if status is not None else "output closed"
    return LeaseDeclinedError(
        f"the lease command ended before naming a device ({ended})" + (f": {said}" if said else "")
    )


def _read_answer(stream: IO[bytes], line: list[bytes], answered: threading.Event) -> None:
    """Keep the first line, then discard the rest, so a broker that logs on never blocks."""
    with stream, contextlib.suppress(OSError, ValueError):
        try:
            line.append(stream.readline(_MAX_ANSWER_BYTES))
        finally:
            answered.set()
        while os.read(stream.fileno(), _MAX_ANSWER_BYTES):
            pass


def _keep_tail(stream: IO[bytes], tail: bytearray) -> None:
    """Drain standard error, keeping its end for a decline's reason."""
    with stream, contextlib.suppress(OSError, ValueError):
        while chunk := os.read(stream.fileno(), _STDERR_TAIL_BYTES):
            tail.extend(chunk)
            del tail[:-_STDERR_TAIL_BYTES]


def _releaser(
    proc: subprocess.Popen[bytes], release_timeout: float, released: threading.Event
) -> Callable[[], None]:
    """End the lease once: a termination signal, a bounded wait, then a forced end."""
    lock = threading.Lock()
    done = False

    def release() -> None:
        nonlocal done
        with lock:
            if done:
                return
            done = True
        if proc.poll() is not None:
            _logger.warning(
                "the lease command %s had already exited (status %s) when its lease was released",
                proc.args,
                proc.returncode,
            )
        else:
            released.set()
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=release_timeout)
            except subprocess.TimeoutExpired:
                # One wedged command must not hold the job's `finally` open.
                _logger.warning(
                    "the lease command %s ignored the termination signal for %g seconds; killing it",
                    proc.args,
                    release_timeout,
                )
                proc.kill()
                try:
                    proc.wait(timeout=release_timeout)
                except subprocess.TimeoutExpired:
                    _logger.warning(
                        "the lease command %s survived a kill; its device may stay lent", proc.args
                    )
        # Also ends the lease of a helper the command left behind (contract point 4).
        if proc.stdin is not None:
            with contextlib.suppress(OSError):
                proc.stdin.close()

    return release


def _parse_answer(line: bytes) -> tuple[str, dict[str, str]]:
    """The device address and environment of the broker's one answer line."""
    try:
        answer = json.loads(line)
    except ValueError as e:
        raise ValueError(f"the lease command's answer is not a JSON object: {e}") from e
    device = answer.get("device") if isinstance(answer, dict) else None
    address = device.get("address") if isinstance(device, dict) else None
    if not isinstance(address, str) or not address.strip() or "," in address:
        raise ValueError(
            "the lease command's answer names no usable `device.address` "
            "(a non-empty string without a comma)"
        )
    env = answer.get("environment", {})
    if not isinstance(env, dict) or not all(
        k and "=" not in k and isinstance(v, str) and "\0" not in k + v for k, v in env.items()
    ):
        raise ValueError(
            "the lease command's `environment` must map variable names to string values"
        )
    return address.strip(), dict(env)
