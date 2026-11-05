#!/usr/bin/env bash
# Prove `bajutsu repl --attach` drives the app already running on a booted Simulator (BE-0455).
#
# Runs from ios-e2e.yml's `actuation` job, after a step that synced the venv. It launches the
# showcase app through simctl, attaches the shell to it, reads one element, and leaves — then fails
# unless the app's process id is the same before and after. A different id would mean the shell
# replaced the process it was asked to keep, which is the one thing attach exists to avoid. It then
# stops the app and attaches again, checking the fallback announces its launch and that the app it
# launched outlives the shell.
#
# Usage: scripts/repl_attach_e2e.sh <udid>
set -euo pipefail

udid="${1:?usage: $0 <udid>}"
bundle=com.bajutsu.showcase.ios.swiftui

# The app's live pid from the guest launchd, or nothing: the same `UIKitApplication:<id>[` job and
# numeric-PID rule the attach probe itself reads (`simctl.Env.is_app_running`).
pid_of() {
  xcrun simctl spawn "$udid" launchctl list |
    awk -v job="UIKitApplication:${bundle}[" 'index($3, job) == 1 && $1 ~ /^[0-9]+$/ { print $1 }'
}

# One shell session reading stable.row.1; prints the shell's output and fails on a non-zero exit,
# printing that output first so the CI log shows why the attach failed.
attach() {
  local rc=0 out
  out="$(printf 'find stable.row.1\nexit\n' | uv run --no-sync bajutsu repl --target showcase-swiftui \
    --udid "$udid" --attach --config demos/showcase/showcase.config.yaml)" || rc=$?
  printf '%s\n' "$out"
  if ((rc != 0)); then
    echo "repl --attach e2e: the shell exited $rc" >&2
    exit 1
  fi
  # Anchored to a table row as `render_table` prints it: the shell's miss message and an error line
  # both echo the id too, so a bare substring match would pass on an empty tree.
  if ! grep -qE '^stable\.row\.1[[:space:]]' <<<"$out"; then
    echo "repl --attach e2e: the attached shell did not read stable.row.1" >&2
    exit 1
  fi
  ATTACH_OUT="$out"
}

# 1. Attach to a running app: no launch, and the same process afterwards.
xcrun simctl launch "$udid" "$bundle"
before="$(pid_of)"
if [[ -z "$before" ]]; then
  echo "repl --attach e2e: $bundle is not running after simctl launch" >&2
  exit 1
fi
attach
after="$(pid_of)"
if grep -q 'was not running' <<<"$ATTACH_OUT"; then
  echo "repl --attach e2e: the shell launched the app instead of attaching to pid $before" >&2
  exit 1
fi
if [[ "$before" != "$after" ]]; then
  echo "repl --attach e2e: the app's pid changed from $before to ${after:-<none>}" >&2
  exit 1
fi
echo "repl --attach e2e: pid $before kept across the attach session"

# 2. The fallback: an installed app that is not running is launched, and outlives the shell.
xcrun simctl terminate "$udid" "$bundle"
# A condition wait, bounded at ~10s: launchd can list the old pid for a moment after terminate, and
# the shell would then take the attach path onto a process that is already gone.
for _ in $(seq 1 50); do
  [[ -z "$(pid_of)" ]] && break
  sleep 0.2
done
if [[ -n "$(pid_of)" ]]; then
  echo "repl --attach e2e: $bundle is still running after simctl terminate" >&2
  exit 1
fi
attach
if ! grep -q "$bundle was not running; launching it" <<<"$ATTACH_OUT"; then
  echo "repl --attach e2e: the shell did not announce launching the stopped app" >&2
  exit 1
fi
if [[ -z "$(pid_of)" ]]; then
  echo "repl --attach e2e: the launched app did not outlive the shell" >&2
  exit 1
fi
echo "repl --attach e2e: the fallback launch announced itself and the app outlived the shell"
