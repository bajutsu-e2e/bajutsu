**English** · [日本語](BE-0390-apm-skill-management-ja.md)

# BE-0390 — Manage agent skills with APM from a single per-skill source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0390](BE-0390-apm-skill-management.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0390") |
| Implementing PR | [#1731](https://github.com/bajutsu-e2e/bajutsu/pull/1731) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

We propose managing Bajutsu's own agent skills with the Agent Package Manager (APM), a dependency
manager for agent context — skills, prompts, instructions, and Model Context Protocol (MCP) servers
— that resolves a manifest into the files each agent host reads. Each skill becomes one source
directory, `.apm/skills/<name>/`, holding a `SKILL.md` and whatever supporting files that skill
needs. One manifest, `apm.yml`, names the skill set and the single host we support, and one command,
`apm install`, writes the deployed tree at `.claude/skills/`, which we commit. The three parallel
trees a skill occupies today — the shared workflow, the Claude Code adapter, and the Codex adapter
behind the `.agents` link — are retired in the same change, since Bajutsu no longer targets Codex.

## Motivation

Adding one skill today means writing three tracked files in three trees: the procedure in
`.agent-workflows/<name>/workflow.md`, a Claude Code adapter in `.claude/skills/<name>/SKILL.md`,
and a Codex adapter in `.agent-hosts/codex/skills/<name>/SKILL.md` with its own `agents/openai.yaml`.
Fourteen skills therefore span more than forty files, and nothing checks that the three copies of a
skill still describe the same procedure. The layout rule lives in `CLAUDE.md` and in the two
`README.md` files under those trees, and a contributor upholds it by hand.

The repository and the convention have already drifted apart. `.claude/skills/.gitignore` declares
`git-sync`, `cleanup`, `task-select`, and `pr-followup` local-only and never committed, yet all four
adapters are tracked — a file already under version control ignores a later `.gitignore` entry. A
contributor cloning the repository consequently cannot tell which skill set they are supposed to
have, and two contributors can hold different sets without either noticing.

Every invocation also pays for the split. An adapter's body instructs the host to read the shared
workflow completely before acting, so the whole procedure enters the context even when a session
needs one step of it. The fourteen workflows total 2,003 lines and 132 KB, and the two largest —
`implement-be` at 23.8 KB and `japanese-document-writing` at 23.5 KB of token-dense Japanese — are
the ones the longest sessions load most often. APM's authoring convention holds a `SKILL.md` within
roughly 500 lines and 5,000 tokens and moves the depth into `references/`, which the host loads only
for the step that needs it.

Once the change ships, a reader can check two things. Invoking a skill then reads one `SKILL.md`
within that budget instead of an adapter plus a whole workflow, and reaches a `references/` file only
for the step that needs it. And `apm audit` reports any deployed file that no longer matches its
source, which today nothing reports at all.

## Detailed design

The work breaks down into six independent pieces:

1. **Manifest and source tree.** A root `apm.yml` declares the package name, its version, and
   `targets: [claude]` — the single host, which keeps APM from writing the `.agents/skills/`
   tree it produces for other hosts. Each skill's source is `.apm/skills/<name>/SKILL.md` plus
   optional `references/`, `scripts/`, and `assets/` directories. `.gitignore` gains `apm_modules/`,
   the package cache APM rebuilds from the lockfile. We commit `apm.yml`, `apm.lock.yaml`, and the
   deployed `.claude/skills/` tree, so a fresh clone has a working skill set before anyone runs a
   command. `.claude/skills/.gitignore` goes away with the old layout and all fourteen skills ship
   committed, which resolves the local-only contradiction above: under a committed deployment
   target, an ignored entry would only hide files `apm install` keeps writing and `apm audit` keeps
   reporting.
2. **Skill conversion.** Each of the fourteen skills folds its Claude Code adapter's host-specific
   instructions — the Agent tool, `/loop`, the `pr-review-toolkit` plugin, the per-role models — into
   its single `SKILL.md`. Each also splits its procedure, so the body stays within APM's budget and
   the depth moves to `references/`. Frontmatter passes through verbatim, so `model: haiku` on
   `roadmap-filter` and `be-progress-tracker` survives the move and the model tiering of
   [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) is unaffected.
   Three skills split differently. `document-writing`, `english-document-writing`, and
   `japanese-document-writing` are norm sets that [`CLAUDE.md`](../../CLAUDE.md) mandates applying in
   full rather than step sequences, so each keeps its complete norm set in `SKILL.md`; where a norm
   set exceeds the budget, its `SKILL.md` opens by telling the host to load every `references/` file
   before drafting. Partial loading is what this exception rules out: no step *needs* a norm, so a
   host left to load on demand could apply a subset and still believe it followed the skill.
3. **Retirement of the old trees.** We delete `.agent-workflows/`, `.agent-hosts/`, the `.agents`
   symlink, and the two `README.md` files documenting them. Five roadmap items — BE-0366, BE-0379,
   BE-0380, BE-0383, and BE-0384 — link into `.agent-workflows/` about eighty times across both
   languages; each link is rewritten to the item's new `.apm/skills/<name>/SKILL.md` path in the
   same change. BE-0384 (`record-issue`) needs more than its links rewritten: it is still a
   `Proposal`, and its *Detailed design* and *Progress* checklist prescribe the three-file layout, so
   that design text moves to the single source too — otherwise implementing it later recreates the
   trees this piece deletes. The roadmap linter checks links *into* `roadmaps/` rather than out of
   it, so these would rot silently rather than fail the gate.
4. **Relocation of the textlint runtime.** APM copies every file beneath a skill's source directory,
   including a `node_modules/` a contributor has installed there. The textlint runtime that today
   sits at `.agent-workflows/document-writing/textlint/` therefore moves to a repository path outside
   the skill tree. The `document-writing` skill and the npm entry in
   [`.github/dependabot.yml`](../../.github/dependabot.yml) both point at the new path.
5. **Tooling.** `make skills` runs `apm install --no-policy`; `make lint-skills` runs
   `apm audit --ci --no-policy`, which
   replays the install into a scratch directory and reports any deployed file that differs from its
   source. `make check` gains the audit step, skipping it with a notice when the `apm` binary is
   absent, the way `lint-actions` and `lint-secrets` already skip — and, as for those two, CI
   installs a pinned `apm-cli` and runs the audit there, so drift fails a pull request even when a
   contributor's machine skipped the check. The
   [session-start hook](../../.claude/hooks/session-start.sh) installs a pinned `apm-cli` and runs
   `apm install`, so a web session starts from the committed skill set. Every `apm install` call
   site passes `--no-policy`, so the deploy and the audit that replays it run under one
   configuration and neither needs the network; without it 0.28.0 warns about the unreachable
   policy and proceeds, which costs a round trip and reads like a failure. `make hooks` gains one more
   local git setting: a merge driver over APM's committed generated output
   ([`scripts/merge-apm-generated.sh`](../../scripts/merge-apm-generated.sh)) that regenerates both
   `apm.lock.yaml` and the deployed `.claude/skills/**` tree from `.apm/skills/` on a conflict,
   mirroring what
   [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
   already does for `uv.lock`. Without the driver, two branches that edited the same skill leave
   conflict markers among per-file SHA-256 hashes, whose correct value is on neither side, and a
   second conflict in the deployed copy whose hand resolution looks right while leaving a deployment
   its source no longer matches. The
   regenerated lockfile is provisional — git runs a merge driver before writing any merged file, so
   `apm install` reads the pre-merge tree — and `make lint-skills` fails the gate until a
   `make skills` after the resolution refreshes it, exactly as `uv lock --check` backs the `uv.lock`
   driver.
6. **Documentation.** The *Agent skill layout* section of [`CLAUDE.md`](../../CLAUDE.md) is rewritten
   around the single source, and the skill links in [`AGENTS.md`](../../AGENTS.md),
   [`docs/ai-development.md`](../../docs/ai-development.md) with its Japanese mirror,
   `docs/contributor-workflow-tutorial.md` with its Japanese mirror, and
   [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) move to the new paths.

This item touches contributor tooling and documentation only — no `bajutsu/`, no `BajutsuKit/`, and
nothing on the `run` or CI verdict path. Prime directive 1 holds under the new gate step as well:
`apm audit` compares SHA-256 hashes recorded in the lockfile against the working tree, so the check
is deterministic and no language model reaches the gate.

The design rests on behavior we verified against APM 0.28.0 rather than on documentation alone. A
local `.apm/skills/<name>/` deployed to `.claude/skills/<name>/` with frontmatter untouched,
`references/` and `scripts/` carried across, and the executable bit preserved; the one thing APM
rewrote was a cross-skill relative link, repointed at the source tree. `targets: [claude]` alone
produced no `.agents/` tree. `apm.lock.yaml` recorded a SHA-256 per deployed file; a
hand edit to a deployed file came back from `apm audit` as drift, and the next `apm install` restored
it.

## Alternatives considered

- **Keep the three trees and add a consistency linter.** Rejected: a linter would catch a
  forgotten adapter, which is the smaller half of the problem. The duplicated adapters and the
  two-hop load that pulls a whole workflow into the context for one step would both remain.
- **Keep Codex as a second target.** Rejected: nobody on the project works through Codex, and APM
  deploys a non-Claude host's skills to `.agents/skills/`, which is exactly the path the current
  `.agents` symlink occupies. Supporting both would mean reconciling that collision for a host with
  no users.
- **Gitignore the deployed tree instead of committing it.** Rejected: a clone would then have no
  skills until someone installed APM and ran it, which is the opposite of the uniformity this item
  is for. APM's own guidance is to commit deployed files. The cost is that each skill's bytes are
  tracked twice, once as source and once as deployment, and keeping `SKILL.md` inside APM's size
  budget bounds that cost.
- **Put plugins, MCP servers, and hooks in `apm.yml` too.** Deferred rather than rejected: APM can
  deploy all three, but its hooks primitive claims `.claude/settings.json`, which this repository
  maintains by hand for the SessionStart hook. Skills alone give the uniformity this item argues
  for; the rest can follow once the settings file's ownership is settled.
- **Install skills from external registries.** Out of scope here: APM resolves a remote package
  through `api.github.com`, which some sandboxes block. This item's subject is Bajutsu's own skills,
  which resolve from the working tree with no network access at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Manifest, source tree, and the committed deployment (`apm.yml`, `.apm/skills/`, the root `.gitignore`, and the removal of `.claude/skills/.gitignore`)
- [x] Conversion of the fourteen skills to one `SKILL.md` each, with depth under `references/`
- [x] Retirement of `.agent-workflows/`, `.agent-hosts/`, and `.agents`, with the roadmap links rewritten and BE-0384's design text moved to the single source
- [x] Relocation of the textlint runtime and its `dependabot.yml` entry
- [x] `make skills`, `make lint-skills`, the `make check` step, the session-start hook, and the `apm.lock.yaml` merge driver wired by `make hooks`
- [x] Documentation: `CLAUDE.md`, `AGENTS.md`, `docs/ai-development.md` (both languages), the contributor tutorial (both languages), and the review contract

## References

- [APM quickstart](https://microsoft.github.io/apm/quickstart/) — the manifest, the lockfile, and
  the install gesture this item adopts.
- [APM skill authoring](https://microsoft.github.io/apm/producer/author-primitives/skills/) — the
  `.apm/skills/<name>/` layout and the `SKILL.md` size budget.
- [APM targets matrix](https://microsoft.github.io/apm/reference/targets-matrix/) — where each host
  receives each primitive, and the source of the `.agents/skills/` collision noted above.
- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) — the model and
  effort tiering carried by the `model:` frontmatter that survives the move.
- [BE-0379](../BE-0379-be-progress-tracker/BE-0379-be-progress-tracker.md),
  [BE-0380](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md), and
  [BE-0384](../BE-0384-record-issue-skill/BE-0384-record-issue-skill.md) — items whose design assumes
  the three-tree layout this item retires. BE-0384 is the one still unbuilt (`Status: Proposal`), so
  its design text, not only its links, moves with the layout.
