**English** · [日本語](BE-XXXX-real-device-collector-hmac-auth-ja.md)

# BE-XXXX — Sign real-device collector traffic instead of sending its token in cleartext

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-real-device-collector-hmac-auth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Security hardening |
| Related | [BE-0115](../BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md), [BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md) |
<!-- /BE-METADATA -->

## Introduction

On a real iOS device, the app reports to Bajutsu's network collector over plain Hypertext Transfer
Protocol (HTTP). Every request carries the run's shared token as a bearer header. Anyone who can
read that traffic can copy the token. With the token, that party can write fabricated exchanges into
the run's evidence.

This item keeps the token on both ends and takes it off the wire. The app signs each request with a
keyed-hash message authentication code (HMAC), keyed by the token. The collector signs its answers
the same way. A collector bound beyond the loopback then refuses the bearer header outright. The
loopback routes, the iOS Simulator and Android, keep working unchanged.

## Motivation

The collector is the HTTP server on the host that receives an app's reports during a run. Those
reports are network exchanges, screen transitions, and control-channel traffic.
[BE-0115](../BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md) gave the
collector a per-run token. The app sends that token as `Authorization: Bearer <token>`. BE-0115
assumed a collector on the loopback alone. A process that could read the token there already shared
the machine.

The real-device host channels item breaks that assumption on purpose. Its pull request,
[#2143](https://github.com/bajutsu-e2e/bajutsu/pull/2143), lets an iPhone report to the host. An
iPhone shares no loopback with the Mac. The collector binds every interface (`::`) instead, and the
app reaches it at a host address offered at launch. The token now crosses the network in cleartext,
on two kinds of request:

- **The probe.** Before it picks a collector, the app sends `GET /ping` with the token to every
  candidate address at once. With no explicit address, the candidates are every routable address
  on the host's interfaces. The token can thus reach an address that belongs to no collector.
- **Every later request.** Each report, each `GET /commands` drain, and each acknowledgement repeats
  the bearer header on the chosen route.

A party that reads one of those requests holds the token for the rest of the run. Three threats then
open, and the first one breaks the deterministic verdict that Bajutsu exists to give:

| Threat | Mechanism | Effect on the run |
|---|---|---|
| Forged evidence | POST a fabricated exchange or transition with the stolen token | A `request` or `response` assertion passes or fails on traffic that never happened |
| Stolen commands | Drain `GET /commands` ahead of the app | A control-channel command ([BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md)) never reaches the app, and its step times out |
| Tampering in transit | Rewrite the `/commands` answer on the route | The app applies a stub table or toggle that the scenario never declared |

The third threat needs no token. Nothing in an answer proves that the collector wrote it, so a party
on the route can rewrite one freely.

The real-device docs suggest a mitigation: pin `BAJUTSU_HOST_ADDRESS` to the CoreDevice tunnel's
address on a shared network. That pin narrows the route, but a reader of the route still sees the
token. The pin also cannot help on a network the team does not control. On Amazon Web Services (AWS)
Device Farm, the reserved device sits on the farm's network
([BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)).

The observable outcome has three parts. Each holds on a USB-attached iPhone that runs the showcase
`network_mock` scenario:

- A packet capture of the run on the host shows no token value in any request.
- A report replayed from that capture gets a 409 and adds nothing to `network.json`.
- A request that presents the bearer header to the network-bound collector also gets a 401.

The same scenario on the Simulator and on Android passes unchanged.

## Detailed design

### What this item protects, and what it leaves out

The adversary can read, replay, or rewrite HTTP traffic between the device and the host. The
adversary may also hold one of the candidate addresses. The design protects four properties:

- **The token stays secret.** No request or answer carries the token, or a value that reveals it.
- **Requests are authentic.** The collector accepts a request from a holder of the token alone.
- **Requests are fresh.** The collector refuses a request that it has accepted before.
- **Answers are authentic.** The app trusts a `/commands` or probe answer that the token holder
  wrote for that very request, and no other.

The design leaves body confidentiality out. An exchange's captured body still travels in cleartext.
Transport Layer Security (TLS) is the tool for that, and *Alternatives considered* keeps it as a
conditional follow-up.

The design also leaves availability out. A party on the route can drop or delay any request. A party
that reads a request can deliver a copy to the collector ahead of the app's own. Either way, the step
that depends on the request fails loudly on its existing timeout, and no forged exchange or command
reaches the verdict. The *Stolen commands* threat thus shrinks to that loud failure; it does not
vanish.

The design also leaves the token's delivery alone. The token reaches the app in the launch
environment (`BAJUTSU_COLLECTOR_TOKEN`). `xcodebuild` hands that environment to the device inside Xcode's own
paired-device connection (USB, or the CoreDevice tunnel). That connection is not the cleartext HTTP
that the reports use, and the token never appears in a report request.

### Signed requests

BajutsuKit signs every request that it sends to the collector:

- report POSTs, for exchanges and for transitions;
- `GET /commands` and `POST /commands/ack`;
- `GET /ping`.

A request carries one header:

```
Authorization: Bajutsu-HMAC-SHA256 nonce=<nonce>, signature=<signature>
```

- `nonce` is 16 random bytes, fresh for every request, in unpadded base64url.
- `signature` is `HMAC-SHA256(token, canonical)`, in unpadded base64url.
- `canonical` joins five lines with a newline, in this order:
  1. the literal `bajutsu-request-v1`;
  2. the HTTP method;
  3. the path as the request line carries it: still percent-encoded, without its query string, and
     `/` when the URL has no path;
  4. the nonce;
  5. the lowercase hex SHA-256 digest of the body (of the empty string for a GET).

The canonical form leaves out the host and port. The collector binds every interface and cannot tell
which of its addresses a request named. An address in the signature would add nothing it could check.
The probe signs each candidate's `GET /ping` with its own nonce, as every other request does. Several
candidates can reach one collector, through Wi-Fi and through the CoreDevice tunnel, for example.
With one shared nonce, the collector would accept the copy that arrived first and refuse the rest.
Arrival order would then replace the host's preference order. The version line
lets a later scheme change the canonical form without a misread on either side.

The collector checks the header first. A missing or malformed header gets a 401 before the collector
reads the body. A nonce that the collector has already accepted gets a 409, also before the body.
Both of those answers leave the request body unread, so both set `close_connection`, as today's
bearer refusal does (`bajutsu/common/evidence/network/_functions.py`). A reply that skips the body
desynchronizes a reused connection, and the reject path stays safe whatever protocol version the
handler speaks.
Then the collector reads the body, recomputes the signature, and compares in constant time
(`hmac.compare_digest`). A mismatch gets a 401, as a wrong token does today.

The collector records a nonce once its signature matches, and no earlier. It checks and records the
nonce in one step under its lock, and a copy that loses that race gets a 409. Two copies of one
request, handled at once on the server's threads, then cannot both pass. A forged header that reuses
a nonce read in transit cannot lock out the genuine request either, since a forgery never matches.

Reading the body before the verdict is the one new exposure, because the bearer check refused a
stranger before reading anything. A fixed cap on the body would be the obvious guard, and it would
break capture. BajutsuKit reports bodies whole on purpose: a JSON body cut mid-object fails a
`responseSchema` assertion on a valid payload. The collector sets no cap for that reason. It feeds the body
through SHA-256 as it reads, holding a fixed amount in memory and spooling the rest to a temporary
file (`tempfile.SpooledTemporaryFile`). A sender without the token thus costs bounded memory, and a
large signed body still reaches `network.json` whole. On a mismatch, the collector drops the spool.
A stranger can still make the collector write a large spool before the mismatch. That cost is one
of availability, which this item leaves out.

### Freshness without a clock

The collector refuses a nonce that it has already accepted. It keeps every accepted nonce in a set
for its whole lifetime, which is one run. `clear()` between scenarios leaves the set alone. A request
captured in one scenario must stay refused in the next.

The set grows with every signed request the run accepts: each report, transition, and
acknowledgement, plus each `/commands` poll. Its memory grows linearly, at about 100 bytes per
nonce in a Python set, and has no fixed upper bound. Two facts keep that cost in proportion:

- **Reports.** An exchange or transition report already leaves its whole record in the collector's
  memory, which is far larger than its nonce. The nonce adds a small fraction to a cost the run
  already pays.
- **Polls.** The control channel polls `/commands` every 0.15 seconds, some 24,000 polls an hour.
  Polls store nothing else, so they set the floor: about 2.4 MB per hour of polling.

A run of 100,000 signed requests thus holds about 10 MB of nonces. The implementation records the
set's size in the run log, so a run whose nonce memory matters is visible rather than inferred.

A timestamp in the canonical form would bound the set, but the device's clock is not the host's. A
skew window would turn a slow clock into a flaky rejection, against prime directive 2. A per-run
counter fails for another reason. BajutsuKit sends reports on concurrent `URLSession` tasks, so
requests arrive out of order. A strictly rising counter would refuse valid requests.

A reused nonce gets a 409, not a 401, for BajutsuKit's sake. The control channel treats a 401 as
terminal and stops polling for the rest of the process: a wrong token is a misconfiguration that no
retry fixes. A reused nonce means instead that a copy of the request arrived first. One forwarded
poll must not end the channel for every later scenario. BajutsuKit thus treats a 409 as
not-answered and keeps polling, and the probe treats it the same way.

### Signed answers

The collector signs every answer that it gives a signed request, including a 204 with no body. The
answer carries one header:

```
X-Bajutsu-Signature: <HMAC-SHA256(token, answer-canonical)>
```

The value is unpadded base64url, as in a request. `answer-canonical` joins four lines with a newline,
in this order:

1. the literal `bajutsu-answer-v1`;
2. the request's nonce;
3. the status code, in decimal;
4. the lowercase hex SHA-256 digest of the answer body.

The request's nonce binds each answer to one request. A captured answer thus fails on any later
poll. A 401 or a 409 carries no signature. For a 401, the collector could not authenticate the request to
bind the answer to. For a 409, the nonce is already spent on another copy.

An unsigned status is forgeable, and two of them are terminal. BajutsuKit's control channel ends
its poll loop on a 401 or a 404, so a party that rewrites one `/commands` answer into either status
stops the channel for the rest of the process. The item keeps both statuses unsigned and accepts
that outcome. The party gains no forged command or exchange, and the host's acknowledgement wait
fails loudly on its existing timeout. The outcome thus sits under the availability exclusion, as
*Stolen commands* does. Signing the 401 as well would not close it cleanly: a genuinely wrong token
also yields an unverifiable 401, and honoring a terminal status only when verified would leave a
misconfigured app polling for the life of the process.

BajutsuKit verifies an answer before it uses the answer:

- **`GET /commands`.** BajutsuKit discards an answer that fails verification, and the device log
  records the discard. The app applies nothing. The host's acknowledgement wait then fails on its
  existing timeout, which names the unacknowledged command.
- **`GET /ping`.** A candidate wins the search when it answers 204 with a valid signature. A host
  that answers an unsigned 204 at a candidate address no longer wins.
- **Report POSTs and acknowledgements.** The app ignores these answers today, and it verifies none
  of them. The collector still signs them, to keep its rule uniform.

### The probe becomes a challenge and response

The two mechanisms above turn the probe into a challenge and response, with no new route. The app
sends each candidate a signed `GET /ping`, and the fresh nonce is the challenge. The real collector
alone can sign an answer bound to that nonce. A candidate that is not the collector learns a nonce
and a signature, and neither one reveals the token. A candidate that relays the probe to the real
collector, though, wins the search with the collector's own signed answer, since the canonical form
leaves out the host and port. The app then reports through that party for the rest of the run. The
party still cannot forge or alter a signed request or answer, so what it gains is confined to the
two properties that this item leaves out: it reads the bodies, and it drops or delays a request.

The real-device item recorded a different follow-up for the probe: an unauthenticated `/ping` that
returns a per-run nonce. That route would send the token to the proven collector alone. The signed
probe meets the same goal without a second, unauthenticated route on the collector.

A party that reads a signed `/ping` can forward it to the real collector. One of the two copies then
wins, and the collector refuses the other. When the forwarded copy wins, the app's own probe gets a
409. The app loses one probe round, and it already retries every second for two minutes.

### Bearer tokens on the loopback alone

The collector accepts both schemes on the loopback, and the signed scheme alone beyond it:

| Collector binding | `Bajutsu-HMAC-SHA256` | `Bearer` |
|---|---|---|
| Loopback (`127.0.0.1`): Simulator, and Android through `adb reverse` | Accepted | Accepted |
| Every interface (`::` or `0.0.0.0`): real iOS device | Accepted | 401 |

The collector decides from the address that it bound in `start()`. Neither the pool nor the
environment needs a new configuration key for that decision. Two clients keep the bearer header on
the loopback:

- **Android.** `BajutsuNet.kt` in `BajutsuAndroid` sends the bearer header over `adb reverse`. That
  route ends on the host's loopback and never crosses the network.
- **An app built against an older BajutsuKit.** A team that updates Bajutsu before it rebuilds its
  app keeps a working Simulator run.

On a real device, an older BajutsuKit's bearer header gets a 401, and the run records no exchanges.
That outcome must name its cause. The collector counts the refused bearer headers whose value
matches this run's token, compared in constant time; every other bearer header gets the same 401
uncounted. A host that reaches the all-interface listener with a made-up value then cannot raise a
false warning. When the count is above zero, the run log carries one warning. The warning says that the app's BajutsuKit
predates signed requests and needs a rebuild for a real-device run.

### The host announces the scheme

The opposite skew needs care too. Apps take BajutsuKit as a separate package, and an app may link a
newer BajutsuKit than the Bajutsu that drives it. A kit that always signed would get a 401 on every
request from an older collector, even on the Simulator. The older collector would print no warning,
and the run would record nothing.

The host announces the scheme instead. The pool injects `BAJUTSU_COLLECTOR_AUTH=hmac` beside
`BAJUTSU_COLLECTOR_TOKEN`, on every route. BajutsuKit signs when the variable says `hmac`, and sends
the bearer header when the variable is absent. An older Bajutsu injects no variable, and a newer kit
then talks to it as an older kit would. The announcement arrives in the launch environment, beside
the token, and a party on the network route cannot strip it. The fallback thus opens no downgrade.

### Changes by side

**Collector (Python).** The collector changes land in `bajutsu/common/evidence/network/`, and the
announcement in `bajutsu/common/runner/pool.py`:

- A small module holds both canonical forms, signing, and verification. The collector and its tests
  share that one definition.
- The pool injects `BAJUTSU_COLLECTOR_AUTH=hmac` beside the token.
- `NetworkCollector` gains the nonce set, the refused-bearer count, and the bearer policy that
  follows the binding. `check_token` stays, for the bearer path on the loopback.
- The handler's `_authenticated` dispatches on the `Authorization` scheme. It hashes and spools
  the body as it reads, and returns the verified nonce. Every answer path signs with that nonce.
- The standard library covers both building blocks (`hmac` and `hashlib`). The base install gains
  no dependency.

**BajutsuKit (Swift).** CryptoKit's `HMAC<SHA256>` covers the signing. CryptoKit ships with iOS 13,
and BajutsuKit's floor is iOS 15.

- `BajutsuNet.postJSON` and `BajutsuNet.pingCollector` sign their requests. `BajutsuScreen`'s
  transition report and the control channel's acknowledgement already go through `postJSON`.
- `BajutsuControlChannel`'s drain signs its `GET` and verifies the answer before decoding it.
- The probe verifies the answer's signature before it counts a candidate as the collector.
- BajutsuKit reads `BAJUTSU_COLLECTOR_AUTH` and signs when the host announces `hmac`. A current
  Bajutsu announces it on every route, the Simulator's included. The Simulator's end-to-end job then
  exercises signing on every run, and the bearer path stays for an older host alone.
- The control channel and the probe treat a 409 as not-answered and keep going.

**Docs.** In both languages:

- `docs/ios-device-cloud.md` replaces its cleartext warning. It states what the route now protects,
  and what it still exposes (the bodies).
- `docs/architecture.md` describes the two schemes and the binding rule beside the collector.

### Prime directives

The change adds no model call. Verification is a pure function of the token, the request, and the
nonce set (prime directive 1). The change adds no wait and reads no clock (prime directive 2). It
adds no configuration key either; the pool injects the announcement, and no user configures it. The binding alone decides the policy, and every target behaves the
same way (prime directive 3).

### Verification

The fast gate covers the collector without a device:

- The collector accepts a request whose signature matches.
- The collector refuses a request when the signature does not match its body, method, or path.
- The collector answers 409 to a request whose nonce it accepted before.
- Of two concurrent copies of one signed request, the collector accepts one and never both.
- While it reads a large body whose signature does not match, the collector stays within the spool's
  memory bound, then drops the spool. It records a large signed body whole.
- The collector accepts a bearer header on a loopback binding.
- On an all-interface binding, the collector refuses a bearer header that carries the run's token,
  counts it, and logs the warning.
- On an all-interface binding, the collector refuses a bearer header with any other value, and
  neither counts it nor logs a warning.
- Every authenticated answer carries a signature that verifies against the request's nonce.
- Fixed vectors pin both formats, including a report POST to the collector's bare URL, whose path
  is `/`. A request vector holds the token, method, path, nonce, body, and
  expected signature; an answer vector holds the token, nonce, status, body, and expected signature.

BajutsuKit's unit tests check the same vectors from Swift, and the two sides then agree byte for
byte. More Swift tests cover the answer side and the announcement:

- The control channel discards an answer with a bad signature.
- The probe rejects a candidate that answers an unsigned 204.
- The control channel keeps polling after a 409, and stops after a 401.
- The kit sends the bearer header when `BAJUTSU_COLLECTOR_AUTH` is absent.

The device proof is manual. It checks the three observations from *Motivation*:

- no token in a host packet capture;
- a replayed report refused;
- a bearer request refused.

### Out of scope

- **Encrypting the bodies.** *Alternatives considered* keeps pinned TLS as the follow-up for a
  deployment that needs it.
- **Android.** Its route ends on the host's loopback and does not cross the network.
- **The channels from the host to the device.** The runner, the `nativeZ` responder, and the WebView
  bridge listen on the device's loopback. Their traffic rides usbmuxd and never reaches the network.
- **Rotating the token within a run.** The token lives for one run already. The nonce set covers
  that whole lifetime.

## Alternatives considered

- **TLS with a pinned per-run certificate.** The host would mint a self-signed certificate per run.
  It would inject the public-key hash next to the token, and BajutsuKit would pin that hash.
  - Gain: TLS protects everything this item protects, plus the bodies.
  - Cost on the host: a certificate generator. That means `cryptography` as a new base dependency,
    or a call to `openssl`.
  - Cost in BajutsuKit: a trust-evaluation delegate on every `URLSession`. The probe would also
    finish a TLS handshake with every candidate.
  - Status: a conditional follow-up, not a rejection. A deployment may need the bodies kept secret,
    such as an app whose test traffic carries real personal data on a shared Device Farm network.
    Then TLS goes on top. The signed scheme can stay inside TLS as request authentication, or give
    way to TLS.
- **A nonce from an unauthenticated `/ping` alone.** The real-device item's follow-up note proposed
  this route. The probe would then send the token to the proven collector alone. Every later request
  would still carry the token in cleartext, and a reader of the route would still get the token.
- **HMAC on every route, with the bearer header retired.** One authentication path is simpler to
  reason about. The cost falls on two parties with nothing to gain:
  - `BajutsuAndroid` needs a Kotlin rewrite for a route that never leaves the loopback.
  - A Simulator user's app records nothing until a rebuild.
- **A configuration switch that turns signing on.** Compatibility would be perfect. The default
  would keep the exposure that this item removes, though. The switch would also add a key that every
  real-device target must remember to set.
- **A timestamp, or a per-run counter, in place of the nonce set.** *Freshness without a clock* gives
  the reason. The device's clock is not the host's, and concurrent reports arrive out of order.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Collector: canonical forms, signing, verification, and the shared test vectors.
- [ ] Collector: scheme dispatch, the spooled body read, the nonce set with 409, and signed answers.
- [ ] Collector: bearer refused beyond the loopback, with the refusal count and its run-log warning.
- [ ] Pool and BajutsuKit: the `BAJUTSU_COLLECTOR_AUTH` announcement and the bearer fallback.
- [ ] BajutsuKit: signed requests from `postJSON`, the probe, and the control channel's drain.
- [ ] BajutsuKit: answer verification and 409 handling in the probe and the control channel.
- [ ] Docs in both languages: `docs/ios-device-cloud.md` and `docs/architecture.md`.
- [ ] Manual real-device proof on `network_mock`: packet capture, replayed report, bearer request.

## References

- [BE-0115](../BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md): the per-run
  token and the bearer check, which this item keeps on the loopback.
- [BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md): the control
  channel, whose `/commands` answers this item signs.
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md): the
  device-cloud route, where the network is not the team's to control.
- [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md): the Android
  collector route, which stays on the loopback.
- [#2143](https://github.com/bajutsu-e2e/bajutsu/pull/2143): the real-device host channels item. It
  binds the collector beyond the loopback and records the cleartext token as a follow-up.
- [RFC 2104](https://www.rfc-editor.org/rfc/rfc2104): the HMAC construction.
