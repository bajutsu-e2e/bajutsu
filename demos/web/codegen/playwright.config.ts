// Playwright runner config for the codegen real-compile gate (BE-0293). The generated spec bakes in
// BASE_URL = http://127.0.0.1:8787/index.html (from demos/web/demo.config.yaml), so `webServer`
// serves demos/web/app on that exact port — the same static app `make -C demos/web e2e` drives,
// only here through the emitted native test rather than the bajutsu runtime. Condition-wait on
// readiness (Playwright polls `url`), never a fixed sleep — the runner's discipline carried over.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  // Fail the run if a generated test accidentally ships `test.only` (a real-compile gate must run the
  // whole emitted suite, not a hand-narrowed slice).
  forbidOnly: !!process.env.CI,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8787/index.html',
  },
  webServer: {
    command: 'python3 -m http.server 8787 --bind 127.0.0.1',
    cwd: '../app',
    url: 'http://127.0.0.1:8787/index.html',
    // Reuse a server a developer already has running locally; always start a fresh one in CI.
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
