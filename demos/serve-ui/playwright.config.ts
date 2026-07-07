// Playwright harness for the specs `bajutsu codegen --emit playwright` generates from the
// serve-UI dogfood scenarios (make -C demos/serve-ui codegen). The scenarios stay the source of
// truth: the specs are regenerated before every run, never edited by hand. The app under test is
// the same inner serve the bajutsu run drives — Playwright's webServer brings it up on :8799 (the
// generated specs bake that BASE_URL from the target's config) and tears it down after.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './playwright-tests',
  fullyParallel: true,
  reporter: 'list',
  webServer: {
    // cwd is this config's directory; run from the repo root so the config paths resolve.
    command:
      'uv run --directory ../.. bajutsu serve --config demos/web/demo.config.yaml --root demos/serve-ui --port 8799',
    url: 'http://127.0.0.1:8799/',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
