// Minimal ESLint flat config for the serve Web UI's vanilla JS (BE-0129).
//
// A proportionate first guardrail for the serve UI's JavaScript — `bajutsu/templates/serve.*.mjs`,
// ~3.2k lines of untested, build-step-free browser code split into section files (BE-0202), now
// native ES modules (BE-0247): each `import`s what it needs and `export`s its public surface. It
// enables only high-signal, low-noise rules that catch real bugs (a duplicated object key, a
// reassigned const, unreachable code, an accidental assignment in a condition) without a build or a
// test framework. A full component/unit harness (Jest/Vitest) is deferred until the code grows
// enough branching logic to need one.
//
// Deliberately narrow:
//   - `no-unused-vars` is off — the UI exposes many top-level functions reached only from inline
//     HTML handlers, which would read as false positives.
//   - `no-undef` is off — it still needs the full set of browser + ES globals declared (`window`,
//     `fetch`, `document`, …) via the `globals` npm package, which would pull a node toolchain into
//     this Python repo; `node --check` (run by `make lint-js`) already catches syntax errors, and
//     these structural rules need no globals. BE-0247 made each file's cross-module inputs explicit
//     `import`s (so that former obstacle is gone), but the bare browser globals remain, so turning
//     `no-undef` on is still blocked on declaring them — a separate step, deferred with the harness.

export default [
  {
    files: ["bajutsu/templates/serve.*.mjs"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module" },
    rules: {
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-const-assign": "error",
      "no-unreachable": "error",
      "no-cond-assign": ["error", "always"],
      "no-func-assign": "error",
      "use-isnan": "error",
      "valid-typeof": "error",
    },
  },
];
