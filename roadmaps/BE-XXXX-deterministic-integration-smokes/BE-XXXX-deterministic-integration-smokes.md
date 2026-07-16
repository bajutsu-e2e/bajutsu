**English** · [日本語](BE-XXXX-deterministic-integration-smokes-ja.md)

# BE-XXXX — Deterministic on-device smokes for AI and external-service sub-paths

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-deterministic-integration-smokes.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
| Related | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md), [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md), [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md), [BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server.md) |
<!-- /BE-METADATA -->

## Introduction

Three feature areas have a full stack that is AI-driven or external-service-dependent, so it is
correctly never CI-gated (prime directive 1 keeps an LLM off the verdict path): the mailbox / 2FA
email step, autonomous crawl, and the MCP server driving a device. Each has a deterministic
sub-path that could run on a real backend without an LLM or a live external account, and today none
is wired into any lane. This item adds deterministic on-device smokes for those sub-paths, keeping
the AI-judged paths deliberately out of CI.

## Motivation

The mailbox step's only external dependency is an HTTP fetch, injected in every test today
([BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) made it a
registry). A loopback stub mail server makes the real fetch → extract → assert chain runnable
deterministically, with no live provider and no key — so the OTP / email step
([BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)) can be proven end to end without
touching prime directive 1.

Autonomous crawl ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
uses an LLM guide, but the crawl engine over a non-AI guide is deterministic. Its real-device
contract — screen-change detection, crash detection, and the `recover` seam — is entirely scripted
by the fake's `react` hook today, so whether a real app's screen actually changes in response to an
action, and whether `recover` heals a wedged browser, is unobserved. A non-AI crawl over the web
showcase exercises that contract without a model.

The MCP server ([BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server.md))'s `bajutsu_run` tool merely
spawns the deterministic `run` CLI, but no test drives a device through the MCP layer end to end.
A single MCP-to-web round-trip proves the tool actuates a real backend rather than only that it
shells out with the right arguments.

None of these puts an LLM on the verdict path: each is the deterministic slice of an
otherwise-excluded feature, and the boundary is worth writing down so the split stays legible.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Mailbox real-HTTP smoke.** Stand up a loopback stub mail server and run an OTP / email-step
  scenario (with a secret / TOTP) on one real backend (web), asserting the fetch, the extraction,
  and a downstream assertion — no live provider, no key.
- **Non-AI crawl smoke.** Run the crawl engine with a deterministic (non-model) guide against the
  web showcase, asserting screen-change detection and the `recover` seam on a real browser.
- **MCP integration smoke.** Drive `bajutsu_run` (and `bajutsu_doctor`) through the MCP server
  against the web backend for one round-trip, proving real actuation through the MCP layer.
- **Document the AI boundary.** State explicitly which paths (record, crawl-with-a-model, enrich)
  stay out of deterministic CI and why, so the deterministic-versus-AI split stays legible.

## Alternatives considered

- **Leave all three at fake-only coverage.** The deterministic sub-paths are runnable on a real
  backend, and their real-device behavior (the mailbox fetch, crawl's screen-change detection and
  `recover`, MCP actuation) is unobserved. Fake-only leaves a runnable path untested.
- **Gate the AI paths themselves.** Putting the model-driven crawl / record / enrich on a CI gate
  would place an LLM on the verdict path, violating prime directive 1. Only the deterministic
  sub-paths are in scope.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Mailbox real-HTTP smoke: loopback stub mail server + an OTP / TOTP scenario on web.
- [ ] Non-AI crawl smoke: deterministic guide against the web showcase, asserting screen-change detection and `recover`.
- [ ] MCP integration smoke: `bajutsu_run` / `bajutsu_doctor` through the MCP server against web.
- [ ] Document which AI paths stay out of deterministic CI and why.

## References

- [BE-0046 — OTP / email steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)
- [BE-0186 — Mailbox provider registry for the email step](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md)
- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
- [BE-0017 — MCP server](../BE-0017-mcp-server/BE-0017-mcp-server.md)
