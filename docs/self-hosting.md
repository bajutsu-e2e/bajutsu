**English** · [日本語](ja/self-hosting.md)

# Self-hosting the web UI on a single Mac

> Run `bajutsu serve` ([cli](cli.md#serve)) as a long-lived, token-authenticated service on a Mac
> you own, reachable by your team over a private Tailscale network. This is **Tier A** of the
> self-hosting roadmap ([BE-0016](../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)):
> the system that runs **today** on the stdlib server, now that
> [BE-0051](../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
> has added the authentication and input validation that make exposure safe. The multi-tenant
> cloud system (a control plane + a Mac worker pool) is a separate, future tier
> ([BE-0015](../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).

## The macOS constraint

The runner drives an **iOS Simulator**, which needs a **GUI login session** (the Aqua session) —
it will not run from a headless daemon. Every choice below follows from that:

- Run serve as a per-user **`LaunchAgent`** (GUI session), **not** a `LaunchDaemon`.
- **Auto-login** the Mac so a GUI session is recovered after a reboot (FileVault needs one
  interactive login after a cold boot before auto-login proceeds).
- **Disable sleep** so the session stays alive: `sudo pmset -a sleep 0 disablesleep 1`.

## 1. Generate the LaunchAgent

`bajutsu serve --emit-launchagent` prints a launchd plist matching the serve flags you pass, then
exits without starting a server. Pick a strong token and write the plist into your LaunchAgents:

```bash
export TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bajutsu serve --emit-launchagent --config bajutsu.config.yaml --token "$TOKEN" \
  > ~/Library/LaunchAgents/com.bajutsu.serve.plist
chmod 600 ~/Library/LaunchAgents/com.bajutsu.serve.plist   # the plist holds the token
```

The emitted plist:

- runs `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config …` (the same interpreter you
  ran the command with, so it uses your venv) with **`RunAtLoad`** + **`KeepAlive`**;
- puts the token in **`EnvironmentVariables`** (`BAJUTSU_SERVE_TOKEN`) — never in the argv, so it
  isn't visible in `ps`;
- writes stdout/stderr to `~/Library/Logs/bajutsu-serve.{out,err}.log`.

If you use the AI paths (`record`, `--dismiss-alerts`), add `ANTHROPIC_API_KEY` to the plist's
`EnvironmentVariables` dict (it isn't baked in for you). It stays bound to `127.0.0.1`; the next
step is what makes it reachable.

## 2. Load it

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bajutsu.serve.plist
launchctl print gui/$(id -u)/com.bajutsu.serve        # verify it's loaded
```

To reload after editing the plist: `launchctl bootout gui/$(id -u)/com.bajutsu.serve` then
bootstrap again.

## 3. Expose it over Tailscale (recommended)

serve stays on `127.0.0.1`; **Tailscale** publishes it inside your tailnet only — identity-based
access plus automatic TLS, no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only in the tailnet)
```

Teammates open that URL; the UI prompts for the token on first load (the browser then carries a
session cookie). API clients send `Authorization: Bearer $TOKEN`.

> **Do not bind `0.0.0.0` to the public internet.** Even with a token, the safe default is a
> private tailnet. serve refuses a non-loopback `--host` without a token, but a public bind widens
> the surface needlessly. If you need a real internal hostname, front serve with **Caddy** for TLS
> (+ basic auth) and keep it off the open internet.

## Security recap (BE-0051)

A self-hosted serve relies on the hardening from
[BE-0051](../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md):
token auth on every request, `/api/run` and `/api/record` confined to the app's scenarios dir with
validated `backend`/`udid`, a CSRF Origin check plus security headers, and a concurrency cap on run
dispatch. Keep the token secret, keep the Mac on a tailnet, and keep the OS patched.
