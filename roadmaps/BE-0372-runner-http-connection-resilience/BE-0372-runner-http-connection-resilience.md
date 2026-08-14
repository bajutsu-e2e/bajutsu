**English** · [日本語](BE-0372-runner-http-connection-resilience-ja.md)

# BE-0372 — Harden the runner's HTTP server against the connection failures a driver timeout produces

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0372](BE-0372-runner-http-connection-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0372") |
| Implementing PR | [#1609](https://github.com/bajutsu-e2e/bajutsu/pull/1609) |
| Topic | Platform support |
| Related | [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu drives the iOS Simulator through a small HTTP server that runs inside an XCUITest process,
called the *runner*. The Python side — the driver, which implements Bajutsu's one platform-specific
interface — opens a loopback connection per operation, and gives each one a bounded socket timeout:
15 seconds for a read, 30 seconds for an actuation.

This item repairs four defects in how the runner's server handles a connection. Three of them stop
the server serving — one by killing its process, two by wedging it while it stays alive — and the
fourth lets a request the client never finished sending reach a handler. We fix all four, and add
the regression tests that hold them fixed.

The defects share one symptom, which is what makes them worth treating together: the runner stops
answering `GET /health`, and the driver — which has no other liveness signal — reports a timeout it
cannot explain. Every defect below therefore reaches a maintainer as the same uninformative
failure, no matter which one fired.

## Motivation

The most severe defect kills the runner's whole process. Darwin raises `SIGPIPE` on a write to a
socket whose peer has closed, and the signal's default disposition terminates the process. The
server sets neither `SO_NOSIGPIPE` on its accepted sockets nor an ignore disposition for the signal,
so any reply written to a departed peer is fatal. The race that produces such a peer is routine
rather than exotic. A driver-side socket timeout closes the connection while the handler is still
blocked — on the main thread, or behind the mutual-exclusion lock that serialises XCUITest
operations — and that handler then writes its reply into a socket the driver has already abandoned.

Two measurements establish the mechanism rather than merely arguing for it. A standalone program
reproducing the server's write path exits with signal 13 (`SIGPIPE`) when it replies to a peer that
closed abortively; with `SO_NOSIGPIPE` set, the same write returns `EPIPE` and the program survives.
Removing the fix from the server itself reproduces the same death in the test target, where the
XCTest process exits with signal 13 instead of failing an assertion. We also checked the common
assumption that Foundation already suppresses the signal, and it is false: `SIGPIPE` is not ignored
at process start, and constructing a `URLSession` does not change the disposition.

The second defect wedges the server without killing it, which is harder to diagnose. The accept loop
treated every `accept()` failure as terminal and broke out of the loop. The listening socket stays
open when it does, so the kernel keeps completing handshakes into the backlog: every connection the
driver opens still succeeds, is never accepted, and is never answered. A live process serving
nothing is indistinguishable from a dead one over that channel. The failure that fires in practice
here is `ECONNABORTED`, which the kernel reports when a peer disconnects between completing its handshake
and the server's accepting it — precisely what a driver-side health poll does when it hits its own
timeout while still queued in the backlog. `EINTR` is the other routine one, and neither says
anything about the listening socket.

The third defect exhausts the pool of connection handlers. Neither of the two blocking calls in a
handler is bounded, because the accepted socket carries no `SO_RCVTIMEO` or `SO_SNDTIMEO`. A peer
that connects and never sends a request therefore blocks its handler in `recv` for the life of the
process, holding one of the eight concurrent handler slots that
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
introduced. Eight such peers hold every slot, and `/health` — which BE-0287 made concurrent so
that it stays answerable during a long gesture — becomes unanswerable again.

The fourth defect is narrower but worse than its size suggests. The header parser stops reading at
an 8 KiB cap, and it did not check whether the blank line terminating the header had actually
arrived before parsing what it had read. A request whose header overran the cap was therefore parsed
anyway, and the resulting method and path were dispatched. Removing the fix demonstrates the
consequence directly: the handler is invoked with a request the client never finished sending, which
on an actuation endpoint means acting on the device.

The same gap ran through the body, where the consequence is sharper. A read that stopped short of the
declared `Content-Length` left the body empty and dispatched the request anyway, and a declared length
above the 64 KiB cap was silently clamped, so reading the first 64 KiB satisfied the count and a
truncated body passed as a complete one. Most routes answer `400 missing or invalid JSON body` and so
fail loudly, but `/selectAll` and `/copy` read no body at all: a truncated request on either actuates
the device. The receive timeout of the third unit widens the reach of that path rather than narrowing
it, because before the timeout only a peer that closed got there, and now a peer that merely stalls
does too.

We claim no attribution to any specific observed continuous-integration failure. What the evidence
above establishes is that each defect is real, that the first is fatal to the process, and that all
four produce the signature the iOS jobs already fail with. Establishing which of them fired in a
given historical run would need runner captures that no longer exist, so this item does not assert
it.

## Detailed design

Four units, one per defect. Each is independent, and all four land together in the runner's HTTP
server ([`BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift`](../../BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift)).

1. **Suppress `SIGPIPE` on every accepted connection.** Set `SO_NOSIGPIPE` on each accepted
   descriptor, in the accept loop, before any handler can touch it. The option turns a write to a
   departed peer into a plain `EPIPE`, which the existing send loop already treats as "stop
   writing", so no other code changes. We set the option per socket rather than changing the
   process-wide signal disposition, because a library has no business rewriting global state its
   host process may depend on.

2. **Retry a per-connection `accept()` failure instead of ending the loop.** Classify the failure by
   its `errno` and end the loop only when the listening socket itself has failed. `EINTR`,
   `ECONNABORTED`, `EAGAIN`, and `EPROTO` concern one connection, so the loop retries at once.
   Descriptor and memory exhaustion — `EMFILE`, `ENFILE`, `ENOMEM`, and `ENOBUFS` — are transient
   too, but retrying them immediately would spin against a condition only time relieves, so those
   pause briefly first. Everything else, including the `EBADF` that `stop()` produces by closing the
   listening socket, ends the loop as before.

3. **Bound both blocking calls with socket timeouts.** Set `SO_RCVTIMEO` and `SO_SNDTIMEO` on each
   accepted descriptor, at ten and 30 seconds. A request's bytes follow its connect over loopback
   within microseconds. A read that stalls for ten seconds is therefore a peer that died or never
   sent a request, never a merely slow peer. A reply, by contrast, is written to a peer that is
   waiting for it, so only a peer that stopped reading without closing stalls a send. Thirty seconds
   matches the wider of the driver's own two limits — 15 seconds for a read, 30 for an actuation —
   rather than exceeding it, which suffices because the only reply large enough to approach the bound
   is a screenshot's PNG, and that one travels the 15-second read path. A timed-out read surfaces as `EAGAIN`, which the parser already
   treats the same way as a closed peer.

4. **Reject a request the client never finished sending.** One invariant, covering the header and
   the body alike. Track whether the parser found the blank line that terminates the header, and
   report the request as unparseable when it did not. Hold the body to the same rule: a read that
   stops short of the declared `Content-Length` fails the request, as does a declared length that
   does not parse, is negative, or exceeds the 64 KiB cap. Yielding a request with a missing body
   instead would leave the call to each route, and `/selectAll` and `/copy` read no body at all, so
   either would actuate the device on a truncated request. The caller already answers 400 for an
   unparseable request, so the fix is confined to the parser.

The tests cover each unit, and every one of them fails — or, for the `SIGPIPE` unit, kills the test
process — when its fix is reverted. Two units are verified by reading the socket options back off a
configured descriptor and by enumerating the `errno` classification directly, because neither a
`SIGPIPE` suppression nor an `accept()` failure can be provoked reliably against a real listener.
The other two are end-to-end against a live server: a peer that vanishes mid-reply, and nine silent
peers against the eight handler slots.

## Alternatives considered

- **Ignore `SIGPIPE` process-wide with `signal(SIGPIPE, SIG_IGN)`.** One line, and it covers every
  socket the process owns rather than only the ones this server accepts. Rejected because
  `BajutsuRunner` is a library linked into a host process it does not own: changing a global signal
  disposition would silently alter the behaviour of unrelated code in that host. `SO_NOSIGPIPE`
  achieves the same result with its effect scoped to the descriptors this server created.
- **Retry every `accept()` failure unconditionally.** Simpler than classifying by `errno`, and it
  would fix the wedge just as well. Rejected because it converts the one genuinely terminal case
  into an infinite loop: after `stop()` closes the listening socket, `accept()` fails with `EBADF`
  immediately and for ever, so an unconditional retry would spin a thread at full speed for the life
  of the process.
- **Drop a request whose peer has already disconnected.** Tempting, since a handler queued behind
  the main-thread lock may finish long after the driver gave up, and its actuation then lands during
  a later step. Rejected as out of scope here, on two grounds. Detecting the disconnect is only
  possible before the handler runs, not during the lock wait where the abandonment usually happens,
  so the check would be partial. More importantly, making whether an actuation runs depend on a race
  works against determinism, which is the second prime directive. The question deserves its own item
  rather than a silent change of actuation semantics inside a resilience fix.
- **Read the request header in blocks rather than one byte at a time.** The parser issues one
  `recv` per byte, which costs roughly 150 system calls for a typical request header. Rejected
  because the cost is sub-millisecond over loopback and so cannot account for the multi-second
  timeouts this item addresses; changing the read strategy would also require the parser to handle
  body bytes arriving in the same block, which is a larger change with no measured benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — set `SO_NOSIGPIPE` on every accepted connection, so a reply to a departed peer cannot
      terminate the runner's process.
- [x] Unit 2 — classify an `accept()` failure by `errno` and retry a per-connection one, so the
      accept loop ends only when the listening socket has failed.
- [x] Unit 3 — set `SO_RCVTIMEO` and `SO_SNDTIMEO` on every accepted connection, so a silent peer
      cannot hold a handler slot for the life of the process.
- [x] Unit 4 — reject a header that reaches the size cap without its terminating blank line, and a
      body that stops short of or overruns its declared length, so a partial request never reaches a
      handler.

## References

- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — made the server concurrent and capped handlers at eight so `/health` stays answerable during a
  long gesture. Unit 3 restores that guarantee, which an unbounded read could defeat.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
  — introduced the lock that serialises XCUITest operations before they reach the main thread. The
  wait on that lock is where a handler is usually parked when the driver abandons its connection.
- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — added the driver-side fast-fail for a runner that has gone away. The defects here are the
  server-side causes of the state that fast-fail observes.
- [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) — an open proposal on
  the same server's dispatch queues. It changes scheduling only and does not overlap the connection
  handling this item repairs.
