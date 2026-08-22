**English** · [日本語](BE-XXXX-collector-foreign-payload-guard-ja.md)

# BE-XXXX — Drop a report the collector cannot recognise instead of storing it as a blank record

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-collector-foreign-payload-guard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0115](../BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu observes an app's network traffic through a small collector it runs on loopback. The app
under test posts one record per request it makes, and the collector holds those records in memory for
a step's `request` assertion to check, then writes them to `network.json` as evidence. The same
receiver also accepts screen-transition reports on a separate `/transitions` path, which the
readiness gate and the `settled` wait consult as a positive "a screen transition finished" signal.

This item makes the collector reject a report it cannot recognise. Until now it accepted every JSON
object it was handed: both record types default every field and ignore unknown keys, so validation
never failed, and a payload sharing none of a type's fields became a blank record of that type rather
than an error. We drop such a payload and log its key names instead.

## Motivation

Both consequences have been observed, and they differ in cost.

The cheaper one wastes evidence. A `visual (xcuitest)` job on 2026-08-13 wrote 99 records to one
scenario's `network.json` with every field empty — no method, no address, no path, no headers. The
file was useless as evidence, and the records were indistinguishable from one another, so nothing in
them identified what had sent them. Reproducing the shape locally confirms the mechanism: validating
`{"kind": "screenChanged", "timestamp": 12345.6}`, or even `{}`, against the exchange model yields
output byte-identical to those 99 records. The sender remains unknown, and that is the second half of
the defect rather than an unrelated gap: because the collector neither rejected nor logged the
payloads, no evidence of their origin survives anywhere.

The costlier consequence reaches the readiness gate. A transition record carries only a `kind`, so a
network exchange misdirected to `/transitions` validates into a transition whose `kind` is empty. The
gate then treats that record as real, because it consults only a transition's receive time and never
its `kind`: one misdirected report would make the gate conclude that a screen transition had finished
when none had. The gate is a deterministic condition wait, and the signal it waits for is exactly
what a blank record fabricates.

Neither failure is reachable from the app's own code today, and this item claims no bug in
`BajutsuKit`. Its two reporters post to the right paths, the collector routes those paths correctly,
and we verified both against the running collector. What justifies the change is that the validation
layer cannot tell a misdirected report from a real one, so any future reporter, endpoint, or software
development kit (SDK) version that gets a path wrong is stored rather than caught. A record the
collector manufactures from a payload it did not understand is worse than no record, because a reader
cannot tell the two apart.

## Detailed design

Two units sharing one helper, both in
[`bajutsu/evidence/network.py`](../../bajutsu/evidence/network.py).

1. **Require at least one recognised key before validating.** Derive each record type's recognised
   key set from the model itself — its field names and every alias it accepts on input, `alias` and
   `validation_alias` alike — so the sets cannot drift from the models they describe. A payload
   sharing none of a type's keys is dropped. One recognised key is enough to proceed, which preserves
   the forward compatibility the models were given `extra="ignore"` for: a newer SDK that adds a
   field still validates, and only a payload with nothing in common is refused.

   Requiring *one* key rather than a named one is also what keeps this from collapsing into the
   mandatory-discriminator design rejected below: either model may rename or drop any single field
   and its reports stay recognisable. That margin exists only while a model declares more than one
   key, and the transition model declared just `kind`, which made the guard on that side exactly the
   rejected design. It now also declares the `timestamp` it never reads — the other half of what the
   app puts on the wire — so the margin is real on both sides and stays derived from the model rather
   than from a literal the code would have to keep in step by hand. The two key sets remain disjoint,
   since an exchange names `startedAt` and `durationMs` and never `kind` or `timestamp`, so a report
   sent to the wrong endpoint is still refused.

2. **Log the dropped payload's key names.** Log at warning level, naming the keys the payload
   carried, so the next occurrence identifies its sender rather than leaving an unattributable blank
   record. Log the key names alone and never a value: a report carries request and response bodies
   and headers, which is precisely what the redaction layer exists to keep out of shared evidence.
   Cap the number of keys logged so one absurd payload cannot flood the log.

The collector keeps answering 204 for a dropped payload rather than 400. Both reporters post
fire-and-forget and never read the status, so a status code would reach nobody, while the log reaches
the maintainer reading a failed run.

## Alternatives considered

- **Make the models require their identifying fields.** Give the exchange model a mandatory `method`
  and the transition model a mandatory `kind`, letting validation reject a foreign payload with no
  extra code. Rejected because a missing field would then fail the same way as a malformed one, and
  the two deserve different treatment: a malformed field is dropped silently on purpose, so an SDK
  change cannot break a run mid-flight, whereas a wholly unrecognised payload is the case worth
  logging. Requiring fields would also fail an SDK that legitimately omits one.
- **Reject the payload with 400 rather than logging it.** Louder in principle, and it would put the
  diagnosis at the sender. Rejected because both reporters are fire-and-forget: they never read the
  response, so the status would change nothing observable, and the batch endpoint would have to
  decide a single status for a mixed batch.
- **Route by payload shape instead of by path.** Store each report wherever its fields say it
  belongs, making a wrong path harmless. Rejected because it replaces an explicit contract with
  inference, and because it would silently repair the misdirection this item exists to surface — a
  reporter posting to the wrong endpoint is a defect a maintainer should see, not one the receiver
  should paper over.
- **Log the whole dropped payload.** The most informative for diagnosis. Rejected on the grounds in
  unit 2: a report's values are bodies and headers, the run's most sensitive evidence, and the key
  names alone already identify the sender.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — derive each record type's recognised keys from the model and drop a payload sharing
      none of them, keeping one recognised key sufficient to validate.
- [x] Unit 2 — log a dropped payload's key names at warning level, never a value, with the count
      capped.

## References

- [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
  — added the screen-transition report and the readiness signal that reads it. A blank transition
  record is a false positive for exactly that signal.
- [BE-0115](../BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md) — gave the
  collector its per-run token, which stops another local process injecting fabricated records. This
  item closes the complementary gap: a payload from the right sender that is not a record at all.
