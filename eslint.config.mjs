// Minimal ESLint flat config for the serve Web UI's vanilla JS (BE-0129).
//
// A proportionate first guardrail for `bajutsu/templates/serve.js` — ~2.5k lines of untested,
// build-step-free browser JavaScript. It enables only high-signal, low-noise rules that catch real
// bugs (a duplicated object key, a reassigned const, unreachable code, an accidental assignment in
// a condition) without a build or a test framework. A full component/unit harness (Jest/Vitest) is
// deferred until serve.js grows enough branching logic to need one.
//
// Deliberately narrow:
//   - `no-unused-vars` is off — serve.js exposes many top-level functions reached only from inline
//     HTML handlers, which would read as false positives.
//   - `no-undef` is off — it needs the full set of browser + ES globals declared (the `globals`
//     npm package), which would pull a node toolchain into this Python repo; `node --check` (run by
//     `make lint-js`) already catches syntax errors, and these structural rules need no globals.

export default [
  {
    files: ["bajutsu/templates/serve.js"],
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
