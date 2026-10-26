#!/usr/bin/env node
/**
 * Lighthouse CI server (`startServerCommand` in frontend/lighthouserc.json): the mock API/static server plus a
 * headless Chromium (Playwright's build) with a CDP port. Lighthouse attaches to that browser via
 * `settings.port` instead of launching its own - chrome-launcher cannot start a Linux Chrome under WSL
 * (it detects WSL and looks for a Windows Chrome / Windows temp dir).
 *
 *   UX_GATE_PORT       mock server port (default 4300)
 *   UX_GATE_CDP_PORT   Chromium remote-debugging port (default 9222, must match lighthouserc.json)
 */
import { chromium } from '@playwright/test';
import { startMockServer } from './mock-server.mjs';

const CDP_PORT = Number(process.env.UX_GATE_CDP_PORT || 9222);

const server = await startMockServer();
const browser = await chromium.launch({
  headless: true,
  args: [`--remote-debugging-port=${CDP_PORT}`, '--disable-dev-shm-usage'],
});
process.stdout.write(`[ux-gate] chromium CDP on 127.0.0.1:${CDP_PORT} - lighthouse ready\n`);

async function shutdown() {
  await browser.close().catch(() => undefined);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, shutdown);
