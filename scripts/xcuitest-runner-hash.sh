#!/usr/bin/env bash
# Content hash of the source paths that feed the bundled XCUITest Simulator runner (BE-0292):
# BajutsuKit's shared sources, the Runner target's own sources/host, and its xcodegen spec.
# `make runner-bundle` stamps the hash into build-info.json when it builds; `scripts/serve.sh`
# recomputes it on every launch and rebuilds only on a mismatch — so a stale bundle (built before a
# runner-affecting change, e.g. a new Router.swift endpoint) never silently lingers, while an
# unchanged tree still costs one `find`/`shasum` pass rather than an `xcodebuild` invocation.
# Hashes working-tree file contents (not git history), so uncommitted local edits count too.
set -euo pipefail
cd "$(dirname "$0")/.."

find BajutsuKit/Package.swift BajutsuKit/Sources BajutsuKit/Runner/Host BajutsuKit/Runner/Sources \
  BajutsuKit/Runner/project.yml -type f -print0 |
  sort -z |
  xargs -0 shasum -a 256 |
  shasum -a 256 |
  awk '{print $1}'
