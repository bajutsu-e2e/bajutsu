// Minimal ESLint flat config for the serve Web UI's vanilla JS (BE-0129).
//
// A proportionate first guardrail for the serve UI's JavaScript — `bajutsu/templates/serve.*.js`,
// ~2.5k lines of untested, build-step-free browser code split into section files (BE-0202) that
// concatenate into one inlined <script> sharing a single global scope. It enables only high-signal,
// low-noise rules that catch real bugs (a duplicated object key, a reassigned const, unreachable
// code, an accidental assignment in a condition) without a build or a test framework. A full
// component/unit harness (Jest/Vitest) is deferred until the code grows enough branching logic to
// need one.
//
// Deliberately narrow:
//   - `no-unused-vars` is off — the UI exposes many top-level functions reached only from inline
//     HTML handlers (and across the section files' shared scope), which would read as false positives.
//   - `no-undef` is off — it needs the full set of browser + ES globals declared (the `globals`
//     npm package), which would pull a node toolchain into this Python repo; `node --check` (run by
//     `make lint-js`) already catches syntax errors, and these structural rules need no globals. It
//     also can't see the cross-file globals the section files share, so it would misfire anyway.

export default [
  {
    files: ["bajutsu/templates/serve.*.js"],
    languageOptions: { ecmaVersion: 2022, sourceType: "script" },
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
