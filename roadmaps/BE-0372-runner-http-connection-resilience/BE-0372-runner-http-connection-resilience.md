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

Bajutsu drives the iOS Simulator through a small HTTP server that runs inside an XCUITest process.
We call that server the *runner*. The Python side is the
[driver](../../docs/glossary.md#driver-backend-actuator-platform), which implements the one
platform-specific interface of Bajutsu. It opens a loopback connection per operation. It gives each
connection a bounded socket timeout: 15 seconds for a read and 30 seconds for an actuation.

This item repairs four defects in how the server of the runner handles a connection.

- One defect kills the process of the server.
- Two defects wedge the server while its process stays alive.
- The fourth defect lets a request that the client never finished sending reach a handler.

We fix all four and add the regression tests that hold them fixed.

The defects share one symptom, which is why they belong in one item. The runner stops answering
`GET /health`. The driver has no other liveness signal, so it reports a timeout it cannot explain.
Every defect below reaches a maintainer as the same uninformative failure, whichever one fired.

## Motivation

The most severe defect kills the whole process of the runner. Darwin raises `SIGPIPE` on a write to a
socket whose peer has closed, and the default disposition of the signal terminates the process. The
server sets neither `SO_NOSIGPIPE` on its accepted sockets nor an ignore disposition for the signal.
Any reply written to a departed peer is therefore fatal.

The race that produces such a peer is routine, not exotic.

1. A socket timeout on the driver side closes the connection.
2. The handler is still blocked at that moment, either on the main thread or behind the
   mutual-exclusion lock that serialises XCUITest operations.
3. The handler then writes its reply into a socket that the driver has already abandoned.

Two measurements establish the mechanism instead of merely arguing for it.

- A standalone program reproduces the write path of the server. It exits with signal 13 (`SIGPIPE`)
  when it replies to a peer that closed abortively. With `SO_NOSIGPIPE` set, the same write returns
  `EPIPE` and the program survives.
- Removing the fix from the server itself reproduces the same death in the test target. The XCTest
  process exits with signal 13 instead of failing an assertion.

We also checked the common assumption that Foundation already suppresses the signal. The assumption
is false. `SIGPIPE` is not ignored at process start, and constructing a `URLSession` does not change
the disposition.

The second defect wedges the server without killing it, which is harder to diagnose. The accept loop
treated every `accept()` failure as terminal and broke out of the loop. The listening socket stays
open when that happens, so the kernel keeps completing handshakes into the backlog. Every connection
that the driver opens still succeeds, is never accepted, and is never answered. Over that channel, a
live process serving nothing is indistinguishable from a dead one.

The failure that fires in practice here is `ECONNABORTED`. The kernel reports it when a peer
disconnects between completing its handshake and the accepting by the server. A health poll on the
driver side does exactly that when it hits its own timeout while still queued in the backlog. `EINTR`
is the other routine failure. Neither says anything about the listening socket.

The third defect exhausts the pool of connection handlers. Neither of the two blocking calls in a
handler is bounded, because the accepted socket carries no `SO_RCVTIMEO` or `SO_SNDTIMEO`. A peer that
connects and never sends a request blocks its handler in `recv` for the life of the process. The
handler holds one of the eight concurrent handler slots that
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
introduced.

Eight such peers hold every slot. `/health` becomes unanswerable again, although BE-0287 made it
concurrent so that it stays answerable during a long gesture.

The fourth defect is narrower, but worse than its size suggests. The header parser stops reading at
an 8 KiB cap. It did not check whether the blank line that terminates the header had arrived before it
parsed what it had read. A request whose header overran the cap was parsed anyway, and the resulting
method and path were dispatched. Removing the fix demonstrates the consequence directly. The handler
is invoked with a request that the client never finished sending. On an actuation endpoint, that
means acting on the device.

The same gap ran through the body, where the consequence is sharper.

- A read that stopped short of the declared `Content-Length` left the body empty, and the request was
  dispatched anyway.
- A declared length above the 64 KiB cap was silently clamped. Reading the first 64 KiB satisfied the
  count, and a truncated body passed as a complete one.

Most routes answer `400 missing or invalid JSON body`, so they fail loudly. But `/selectAll` and
`/copy` read no body at all, so a truncated request on either actuates the device.

The receive timeout of the third unit widens the reach of that path. Before the timeout, only a peer
that closed got there. Now a peer that merely stalls gets there too.

We claim no attribution to any specific observed continuous-integration failure. The evidence above
establishes three facts. Each defect is real. The first is fatal to the process. All four produce the
signature that the iOS jobs already fail with. Establishing which of them fired in a given historical
run would need runner captures that no longer exist. This item does not assert it.

## Detailed design

The design has four units, one per defect. Each unit is independent. All four land together in the
HTTP server of the runner
([`BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift`](../../BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift)).

1. **Suppress `SIGPIPE` on every accepted connection.** Set `SO_NOSIGPIPE` on each accepted
   descriptor, in the accept loop, before any handler can touch it.
   - The option turns a write to a departed peer into a plain `EPIPE`. The existing send loop already
     treats `EPIPE` as "stop writing", so no other code changes.
   - We set the option per socket. We do not change the process-wide signal disposition, because a
     library has no business rewriting global state that its host process may depend on.

2. **Retry a per-connection `accept()` failure instead of ending the loop.** Classify the failure by
   its `errno`. End the loop only when the listening socket itself has failed.
   - `EINTR`, `ECONNABORTED`, `EAGAIN`, and `EPROTO` concern one connection. The loop retries at once.
   - Descriptor and memory exhaustion are transient too. They are `EMFILE`, `ENFILE`, `ENOMEM`, and
     `ENOBUFS`. Retrying them immediately would spin against a condition that only time relieves, so
     the loop pauses briefly first.
   - Everything else ends the loop as before. That includes the `EBADF` that `stop()` produces by
     closing the listening socket.

3. **Bound both blocking calls with socket timeouts.** Set `SO_RCVTIMEO` and `SO_SNDTIMEO` on each
   accepted descriptor, at ten and 30 seconds.
   - The bytes of a request follow its connect over loopback within microseconds. A read that stalls
     for ten seconds is a peer that died or never sent a request. It is never a merely slow peer.
   - A reply is written to a peer that is waiting for it. Only a peer that stopped reading without
     closing stalls a send.
   - Thirty seconds matches the wider of the two limits of the driver: 15 seconds for a read and 30
     for an actuation. It does not exceed that limit. That suffices because the only reply large
     enough to approach the bound is the PNG of a screenshot, and that reply travels the 15-second
     read path.
   - A timed-out read surfaces as `EAGAIN`. The parser already treats `EAGAIN` as it treats a closed
     peer.

4. **Reject a request the client never finished sending.** One invariant covers the header and the
   body alike.
   - Track whether the parser found the blank line that terminates the header. Report the request as
     unparseable when it did not.
   - Hold the body to the same rule. A read that stops short of the declared `Content-Length` fails
     the request. So does a declared length that does not parse, is negative, or exceeds the 64 KiB
     cap.
   - Yielding a request with a missing body would leave the call to each route. `/selectAll` and
     `/copy` read no body at all, so either would actuate the device on a truncated request.
   - The caller already answers 400 for an unparseable request, so the fix is confined to the parser.

The tests cover each unit. Every test fails when its fix is reverted. For the `SIGPIPE` unit, the
revert kills the test process instead.

Two units are verified without a live failure. One test reads the socket options back off a configured
descriptor. Another enumerates the `errno` classification directly. Neither a `SIGPIPE` suppression
nor an `accept()` failure can be provoked reliably against a real listener.

The other two units are tested end to end against a live server. One test uses a peer that vanishes
mid-reply. The other uses nine silent peers against the eight handler slots.

## Alternatives considered

- **Ignore `SIGPIPE` process-wide with `signal(SIGPIPE, SIG_IGN)`.** The change is one line. It covers
  every socket the process owns, not just the ones this server accepts. Rejected, because
  `BajutsuRunner` is a library linked into a host process it does not own. Changing a global signal
  disposition would silently alter the behavior of unrelated code in that host. `SO_NOSIGPIPE`
  achieves the same result, with its effect scoped to the descriptors that this server created.
- **Retry every `accept()` failure unconditionally.** The change is simpler than classifying by
  `errno`, and it would fix the wedge just as well. Rejected, because it converts the one genuinely
  terminal case into an infinite loop. After `stop()` closes the listening socket, `accept()` fails
  with `EBADF` at once and for ever. An unconditional retry would spin a thread at full speed for the
  life of the process.
- **Drop a request whose peer has already disconnected.** The idea is tempting. A handler queued
  behind the main-thread lock may finish long after the driver gave up, and its actuation then lands
  during a later step. Rejected as out of scope here, on two grounds.
  - Detecting the disconnect is possible before the handler runs, not during the lock wait where the
    abandonment usually happens. The check would be partial.
  - More importantly, making the question whether an actuation runs depend on a race works against
    determinism, which is the second prime directive. The question deserves its own item. A
    resilience fix should not change actuation semantics silently.
- **Read the request header in blocks rather than one byte at a time.** The parser issues one `recv`
  per byte, which costs roughly 150 system calls for a typical request header. Rejected, because the
  cost is sub-millisecond over loopback. It cannot account for the multi-second timeouts this item
  addresses. A new read strategy would also require the parser to handle body bytes arriving in the
  same block. That change is larger, and it has no measured benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1: set `SO_NOSIGPIPE` on every accepted connection, so a reply to a departed peer cannot
      terminate the process of the runner.
- [x] Unit 2: classify an `accept()` failure by `errno` and retry a per-connection one, so the
      accept loop ends only when the listening socket has failed.
- [x] Unit 3: set `SO_RCVTIMEO` and `SO_SNDTIMEO` on every accepted connection, so a silent peer
      cannot hold a handler slot for the life of the process.
- [x] Unit 4: reject a header that reaches the size cap without its terminating blank line, and a
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
