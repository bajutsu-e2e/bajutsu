**English** · [日本語](docs/ja/security.md)

# Security Policy

## Supported versions

Bajutsu is **pre-alpha**. Security fixes are applied to the `main` branch only;
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
project-specific points worth keeping in mind:

- **API keys / secrets.** Only the AI paths (`record`, `run --dismiss-alerts`)
  need `ANTHROPIC_API_KEY`. Never commit or share API keys; keep them in `.env`
  (gitignored). The deterministic `run`/CI gate needs no secrets.
- **Captured evidence.** Run artifacts under `runs/` (screenshots, page sources,
  logs) can contain sensitive data from the app under test. Review them before
  sharing, attaching to a pull request, or uploading to CI.
- **AI is never the judge.** The deterministic `run` gate involves no LLM; AI is
  used only to author scenarios and investigate failures. Pass/fail comes solely
  from machine-checkable assertions.
