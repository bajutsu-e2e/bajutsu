---
name: be-progress-tracker
model: haiku
description: Create or update a per-BE-item status Artifact (overview, implementation progress, work log). Called by ideation, implement-be, or propose-and-build at their own step boundaries — not run standalone.
---

# Claude adapter

Read `.agent-workflows/be-progress-tracker/workflow.md` completely, then follow it.

- The status page is a Markdown Artifact, published and later redeployed with the Artifact tool.
  Load `artifact-design` before the first write and keep the design plain — this is a glanceable
  dashboard for the human watching the session, not a polished deliverable.
- **One Artifact per BE item, updated in place.** On the first call for a BE id in this session,
  check whether one already exists — `Artifact({action: "list"})`, or a URL the calling workflow
  already has cached from an earlier checkpoint — before publishing a new one; redeploy to the same
  URL on every later call (pass the same `file_path`) rather than creating a fresh Artifact per
  checkpoint. Hand the URL back to the calling workflow so it can reuse it next time. (Both
  `action: "list"` and the `artifact-design` skill referenced above are genuine capabilities of
  this host, not assumed: `list` enumerates the Artifact tool's own actions, and `artifact-design`
  is one of this session's available skills — see the Artifact tool's own description and this
  session's skill listing.)
- Pick the `favicon` and `title` on the first call and keep them stable across every redeploy of
  the same item, per the Artifact tool's own rule; only a hard pivot in what the item is (which
  shouldn't happen mid-implementation) would justify changing them.
- Runs at `model: haiku` by default — the frontmatter above sets that when this skill is invoked
  directly. A calling workflow that dispatches it through the Agent tool instead should pass
  `model: "haiku"` explicitly, since a subagent call doesn't automatically inherit a skill's own
  frontmatter model. The job is transcription and formatting of decisions the caller already made,
  not new judgment, so the cheapest capable model is the right default; a caller can still upshift
  for a particular BE item whose overview is unusually hard to summarize.
