**English** · [日本語](SECURITY.ja.md)

# Security Policy

## Supported versions

Bajutsu is **pre-alpha**. We apply security fixes to the `main` branch only;
there are no released versions to back-port to yet.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue or pull
request, and do not disclose the details publicly until a fix is available.

Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
open the repository's **Security** tab and choose **Report a vulnerability**.

We aim to acknowledge a report within a few days (best effort) and will keep you
informed of progress toward a fix. When you report, please include enough detail
to reproduce the issue — affected commit, steps, and impact.

## Scope and notes

Bajutsu is a defensive end-to-end (E2E) testing tool for the iOS Simulator. A few
project-specific points are worth keeping in mind:

- **API keys / secrets.** Only the AI paths (`record`, `run --alert-handling`)
  need `ANTHROPIC_API_KEY`. Never commit or share API keys; keep them in `.env`
  (gitignored). The deterministic `run`/CI gate needs no secrets.
- **`type` of non-Latin text on the iOS Simulator (idb) backend.** idb's hardware-keyboard
  text path only encodes US-keyboard-layout characters. A `type` step with non-Latin text
  falls back to the Simulator's pasteboard instead. The fallback writes the value with
  `simctl pbcopy` and sends a hardware paste. That pasteboard is readable by any process
  with `simctl` access to the same Simulator instance. Avoid typing a secret or one-time
  passcode that contains non-Latin characters on this backend.
- **Captured evidence.** Run artifacts under `runs/` (screenshots, page sources,
  logs) can contain sensitive data from the app under test. Review them before
  sharing, attaching to a pull request, or uploading to CI.
- **AI is never the judge.** The deterministic `run` gate involves no LLM; we
  use AI only to author scenarios and investigate failures. Pass/fail comes solely
  from machine-checkable assertions.
