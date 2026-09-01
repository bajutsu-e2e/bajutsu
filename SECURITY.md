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

Bajutsu is a defensive end-to-end (E2E) testing tool built on a backend-agnostic driver: the iOS
Simulator (XCUITest), a web (Playwright) backend, and an Android (adb) backend are all landed. A
few project-specific points are worth keeping in mind:

- **API keys / secrets.** Only the AI paths (`record`, `crawl`, `triage --ai`)
  need `ANTHROPIC_API_KEY`. Never commit or share API keys; keep them in `.env`
  (gitignored). The deterministic `run`/CI gate needs no secrets. A tracked pre-commit hook and a
  CI re-scan, both backed by [gitleaks](https://github.com/gitleaks/gitleaks), catch a secret
  before and after it lands on a branch — see
  [`docs/ai-development.md`](docs/ai-development.md#block-a-secret-before-its-committed) — but
  treat that as a backstop, not a reason to paste one in.
- **`setClipboard` writes to a device-wide clipboard.** The `setClipboard` scenario step (used
  for paste flows) seeds the target's clipboard: `simctl pbcopy` on the iOS Simulator; on Android,
  an ordered `am broadcast` to the app's in-app receiver (BajutsuAndroid, BE-0233), which then
  writes the OS clipboard — so it depends on the app under test embedding that receiver. That
  clipboard is readable by any other process with access to the same Simulator or device. Avoid
  seeding a secret or one-time passcode this way.
- **Captured evidence.** Run artifacts under `runs/` (screenshots, page sources,
  logs) can contain sensitive data from the app under test. Review them before
  sharing, attaching to a pull request, or uploading to CI.
- **AI is never the judge.** The deterministic `run` gate involves no LLM; we
  use AI only to author scenarios and investigate failures. Pass/fail comes solely
  from machine-checkable assertions.
