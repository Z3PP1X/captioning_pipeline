import { defineConfig, devices } from '@playwright/test';
import { execSync } from 'node:child_process';
import { resolve } from 'node:path';

/**
 * UX gate - Playwright configuration (run from `frontend/`: `npx playwright test e2e/ux-gate.spec.ts`).
 * The web server is the production build + the deterministic mock API (e2e/mock-server.mjs), so the suite
 * needs neither the Python backend nor real footage.
 */
const REPO_ROOT = resolve(__dirname, '..');
const PORT = Number(process.env.UX_GATE_PORT || 4300);

// Chromium system libraries unpacked by scripts/ux_gate_setup.sh when they cannot be apt-installed
// (same logic as scripts/browser_env.sh, so a bare `npx playwright test` works too).
if (!process.env.LD_LIBRARY_PATH?.includes('chrome-deps')) {
  try {
    const env = execSync(`bash -c 'source "${REPO_ROOT}/scripts/browser_env.sh" && printf %s "$LD_LIBRARY_PATH"'`, {
      encoding: 'utf8',
    });
    if (env) process.env.LD_LIBRARY_PATH = env;
  } catch {
    /* system Chromium dependencies are installed */
  }
}

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 5_000 },
  reporter: [['list'], ['html', { outputFolder: './playwright-report', open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    reducedMotion: 'reduce',
    viewport: { width: 1920, height: 1080 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } } }],
  webServer: {
    command: 'bash scripts/ux_gate.sh serve',
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI && process.env.UX_GATE_REUSE === '1',
    timeout: 240_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
