/**
 * UX GATE - five hard UX laws, checked against the production build + deterministic mock API
 * (e2e/mock-server.mjs). Run: `make ux-gate` or `cd frontend && npx playwright test e2e/ux-gate.spec.ts`.
 *
 *   Law 1  Zero accessibility & contrast violations (axe, WCAG 2.1 A/AA) on every main view
 *   Law 2  100 % mouse-free HITL triage (J / K / C / E / Escape / Enter, no focus trap)
 *   Law 3  Zero layout shift (CLS <= 0.01) while thumbnails load and while switching images
 *   Law 4  Fitts's law (>= 32x32 px hit boxes), no horizontal overflow, no clipped text without a title
 *   Law 5  Immediate feedback (< 100 ms aria-busy + disabled trigger) for slow actions
 *
 * Views are reached through hash deep links (`#/studio/<step>`), so no test depends on clicking around.
 * See .agents/skills/ux-gate/SKILL.md for the self-healing protocol when a law fails.
 */
import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const STUDIO_VIEWS = [
  { name: 'Step 1 - FFmpeg ingest', hash: '#/studio/ingest', ready: '[data-testid="view-ingest"]' },
  { name: 'Step 2 - FFmpeg frame triage', hash: '#/studio/triage', ready: '[data-testid="view-triage"]' },
  { name: 'Step 3 - Dataset grid (tagging)', hash: '#/studio/tagging', ready: '[data-testid="view-tagging"]' },
  { name: 'Step 4 - MOSS 3-tier caption editor', hash: '#/studio/captioning', ready: '[data-testid="view-captioning"]' },
  { name: 'Step 5 - Dataset export & RAG', hash: '#/studio/export_rag', ready: '[data-testid="view-export_rag"]' },
] as const;

const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
] as const;

async function resetMock(page: Page): Promise<void> {
  const res = await page.request.post('/__reset');
  expect(res.ok()).toBeTruthy();
}

async function openView(page: Page, hash: string, ready: string): Promise<void> {
  await page.goto(`/${hash}`);
  await page.locator(ready).waitFor({ state: 'visible' });
  // Let images decode and signal effects settle
  await page.waitForLoadState('networkidle');
}

async function openSettings(page: Page): Promise<void> {
  await openView(page, '#/studio/ingest', '[data-testid="view-ingest"]');
  await page.getByTestId('open-studio-settings').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog', { name: /settings/i }).waitFor({ state: 'visible' });
}

test.beforeEach(async ({ page }) => {
  await resetMock(page);
});

// ================================================================================================ Law 1
test.describe('Law 1 - zero accessibility & contrast violations (WCAG 2.1 AA)', () => {
  for (const view of STUDIO_VIEWS) {
    test(`axe: ${view.name}`, async ({ page }) => {
      await openView(page, view.hash, view.ready);
      const accessibilityScanResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(accessibilityScanResults.violations).toEqual([]);
    });
  }

  test('axe: OpenRouter settings dialog', async ({ page }) => {
    await openSettings(page);
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });
});

// ================================================================================================ Law 2
/** Makes any use of the mouse API throw: the HITL loop must be completable with the keyboard alone. */
function forbidMouse(page: Page): void {
  const banned = () => {
    throw new Error('Law 2: page.mouse is forbidden in the keyboard-only HITL test');
  };
  for (const method of ['click', 'dblclick', 'down', 'up', 'move', 'wheel'] as const) {
    (page.mouse as unknown as Record<string, unknown>)[method] = banned;
  }
}

const activeItem = (page: Page) => page.locator('[data-testid="caption-item"][aria-current="true"]');

/** Press a key and measure (in the page) the time until the selected item changed. */
async function pressAndMeasureSelection(page: Page, key: string): Promise<{ ms: number; id: string | null }> {
  await page.evaluate(() => {
    const w = window as unknown as { __uxSel?: { start: number; end: number | null; id: string | null } };
    const before = document.querySelector('[data-testid="caption-item"][aria-current="true"]')?.getAttribute('data-media-id') ?? null;
    w.__uxSel = { start: 0, end: null, id: null };
    window.addEventListener('keydown', () => (w.__uxSel!.start = performance.now()), { capture: true, once: true });
    const obs = new MutationObserver(() => {
      const now = document.querySelector('[data-testid="caption-item"][aria-current="true"]')?.getAttribute('data-media-id') ?? null;
      if (now !== before && w.__uxSel!.end === null) {
        w.__uxSel!.end = performance.now();
        w.__uxSel!.id = now;
        obs.disconnect();
      }
    });
    obs.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['aria-current'], childList: true });
  });
  await page.keyboard.press(key);
  await page.waitForFunction(() => (window as unknown as { __uxSel: { end: number | null } }).__uxSel.end !== null);
  return page.evaluate(() => {
    const s = (window as unknown as { __uxSel: { start: number; end: number; id: string | null } }).__uxSel;
    return { ms: s.end - s.start, id: s.id };
  });
}

test.describe('Law 2 - 100% mouse-free HITL triage (keyboard-first, no focus trap)', () => {
  test('J / C / E / Escape / Enter complete the review loop without the mouse', async ({ page }) => {
    forbidMouse(page);
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await expect(activeItem(page)).toHaveCount(1);
    const firstId = await activeItem(page).getAttribute('data-media-id');

    // J -> next image in < 50 ms
    const next = await pressAndMeasureSelection(page, 'j');
    expect(next.id).not.toBe(firstId);
    expect(next.ms, `J selection latency ${next.ms.toFixed(1)} ms`).toBeLessThan(50);

    // K -> back again (same contract)
    const prev = await pressAndMeasureSelection(page, 'k');
    expect(prev.id).toBe(firstId);
    expect(prev.ms).toBeLessThan(50);

    // C -> smart-crop proposal toggles on, C again toggles it off
    const cropToggle = page.getByTestId('smart-crop-toggle');
    const overlay = page.getByTestId('smart-crop-overlay');
    await expect(cropToggle).toHaveAttribute('aria-pressed', 'false');
    await page.keyboard.press('c');
    await expect(cropToggle).toHaveAttribute('aria-pressed', 'true');
    await expect(overlay).toBeVisible();
    await page.keyboard.press('c');
    await expect(cropToggle).toHaveAttribute('aria-pressed', 'false');
    await expect(overlay).toBeHidden();

    // E -> focus jumps into caption.long; letters typed there are text, not hotkeys
    const longField = page.getByTestId('caption-long');
    await page.keyboard.press('e');
    await expect(longField).toBeFocused();
    const selectedWhileEditing = await activeItem(page).getAttribute('data-media-id');
    await page.keyboard.press('End');
    await page.keyboard.type(' jkc');
    await expect(longField).toHaveValue(/ jkc$/);
    await expect(activeItem(page)).toHaveAttribute('data-media-id', selectedWhileEditing!);

    // Escape -> leaves the textarea (no focus trap) and returns to the global hotkey mode
    await page.keyboard.press('Escape');
    await expect(longField).not.toBeFocused();
    const activeTag = await page.evaluate(() => document.activeElement?.tagName ?? '');
    expect(['INPUT', 'TEXTAREA', 'SELECT']).not.toContain(activeTag);
    const afterEscape = await pressAndMeasureSelection(page, 'j');
    expect(afterEscape.id).not.toBe(selectedWhileEditing);
    await pressAndMeasureSelection(page, 'k');

    // Enter -> APPROVED + jump to the next unreviewed image
    const approvedId = await activeItem(page).getAttribute('data-media-id');
    await page.keyboard.press('Enter');
    const approvedRow = page.locator(`[data-testid="caption-item"][data-media-id="${approvedId}"]`);
    await expect(approvedRow).toHaveAttribute('data-caption-status', 'approved');
    await expect(activeItem(page)).not.toHaveAttribute('data-media-id', approvedId!);
    await expect(activeItem(page)).not.toHaveAttribute('data-caption-status', 'approved');
    await expect(page.getByTestId('active-caption-status')).not.toHaveText(/approved/i);
  });

  test('Ctrl+Enter in a caption field saves, blurs and returns to navigation mode', async ({ page }) => {
    forbidMouse(page);
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await page.keyboard.press('e');
    const longField = page.getByTestId('caption-long');
    await expect(longField).toBeFocused();
    const saved = page.waitForRequest((r) => /\/api\/v1\/items\/\d+\/caption$/.test(r.url()) && r.method() === 'PUT');
    await page.keyboard.press('Control+Enter');
    await saved;
    await expect(longField).not.toBeFocused();
    const moved = await pressAndMeasureSelection(page, 'j');
    expect(moved.ms).toBeLessThan(50);
  });
});

// ================================================================================================ Law 3
const CLS_INIT = () => {
  const w = window as unknown as { __cls: { value: number; entries: Array<{ value: number; sources: string[] }> } };
  w.__cls = { value: 0, entries: [] };
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as Array<PerformanceEntry & { value: number; hadRecentInput: boolean; sources?: Array<{ node?: Node }> }>) {
      // Strict: count shifts even right after keyboard input - switching images must not move anything
      w.__cls.value += entry.value;
      w.__cls.entries.push({
        value: entry.value,
        sources: (entry.sources ?? []).map((s) => (s.node instanceof Element ? s.node.outerHTML.slice(0, 120) : String(s.node?.nodeName))),
      });
    }
  }).observe({ type: 'layout-shift', buffered: true });
};

async function readCls(page: Page): Promise<{ value: number; entries: Array<{ value: number; sources: string[] }> }> {
  return page.evaluate(() => (window as unknown as { __cls: { value: number; entries: Array<{ value: number; sources: string[] }> } }).__cls);
}

test.describe('Law 3 - zero layout shift (CLS <= 0.01)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(CLS_INIT);
    // Thumbnails and full frames arrive late (slow disk / network): the layout must already reserve their space
    await page.route(/\/api\/(v1\/media|frames)\/\d+\/(thumbnail|preview|full)/, async (route) => {
      await new Promise((r) => setTimeout(r, 400));
      await route.continue();
    });
  });

  test('thumbnail grid (frame triage) does not shift while images load', async ({ page }) => {
    await openView(page, '#/studio/triage', '[data-testid="view-triage"]');
    await page.waitForTimeout(600);
    const cls = await readCls(page);
    expect(cls.value, JSON.stringify(cls.entries, null, 1)).toBeLessThanOrEqual(0.01);
  });

  test('dataset grid (tagging) does not shift while images load', async ({ page }) => {
    await openView(page, '#/studio/tagging', '[data-testid="view-tagging"]');
    await page.waitForTimeout(600);
    const cls = await readCls(page);
    expect(cls.value, JSON.stringify(cls.entries, null, 1)).toBeLessThanOrEqual(0.01);
  });

  test('switching images and captions in the 3-tier editor does not shift', async ({ page }) => {
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await page.waitForTimeout(600);
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('j');
      await page.waitForTimeout(150);
    }
    await page.keyboard.press('k');
    await page.waitForTimeout(600);
    const cls = await readCls(page);
    expect(cls.value, JSON.stringify(cls.entries, null, 1)).toBeLessThanOrEqual(0.01);
  });
});

// ================================================================================================ Law 4
interface GeometryReport {
  smallTargets: string[];
  overflowX: { scrollWidth: number; clientWidth: number } | null;
  clipped: string[];
}

/** DOM geometry audit, executed in the page. */
function auditGeometry(): GeometryReport {
  const describe = (el: Element): string => {
    const text = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50);
    const testId = el.getAttribute('data-testid');
    return `<${el.tagName.toLowerCase()}${testId ? ` data-testid="${testId}"` : ''}> "${text}"`;
  };
  const isRendered = (el: Element): boolean => {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false; // sr-only / visually hidden helpers
    // Inside a closed <details> or an aria-hidden subtree -> not a target
    if (el.closest('[aria-hidden="true"], [inert]')) return false;
    return true;
  };

  const smallTargets: string[] = [];
  for (const el of Array.from(document.querySelectorAll('button, a[href], [role="button"], .chip'))) {
    if (!isRendered(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 32 || r.height < 32) {
      smallTargets.push(`${describe(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }

  const root = document.documentElement;
  const overflowX = root.scrollWidth > root.clientWidth ? { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth } : null;

  const clipped: string[] = [];
  const candidates = new Set<Element>(Array.from(document.querySelectorAll('button, a[href], [role="button"], .chip, label')));
  // Truncating descendants (ellipsis / overflow hidden) inside labels and buttons count as well
  for (const el of Array.from(document.querySelectorAll('button *, a[href] *, [role="button"] *, .chip *, label *, .truncate'))) {
    candidates.add(el);
  }
  for (const el of candidates) {
    if (!(el instanceof HTMLElement) || !isRendered(el)) continue;
    if (!el.textContent?.trim()) continue;
    const style = getComputedStyle(el);
    const clipsX = style.overflowX !== 'visible' || style.textOverflow === 'ellipsis';
    const clipsY = style.overflowY !== 'visible' || Number(style.webkitLineClamp) > 0;
    const overflowsX = el.scrollWidth > el.clientWidth + 1;
    const overflowsY = el.scrollHeight > el.clientHeight + 1;
    const isScrollable = ['auto', 'scroll'].includes(style.overflowX) || ['auto', 'scroll'].includes(style.overflowY);
    if (isScrollable || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') continue;
    if ((clipsX && overflowsX) || (clipsY && overflowsY)) {
      const titled = el.closest('[title]');
      if (!titled || !titled.getAttribute('title')?.trim()) clipped.push(describe(el));
    }
  }
  return { smallTargets, overflowX, clipped };
}

test.describe("Law 4 - Fitts's law & overflow detector", () => {
  for (const vp of VIEWPORTS) {
    test.describe(`${vp.width}x${vp.height}`, () => {
      test.use({ viewport: vp });
      for (const view of STUDIO_VIEWS) {
        test(`geometry: ${view.name}`, async ({ page }) => {
          await openView(page, view.hash, view.ready);
          const report = await page.evaluate(auditGeometry);
          expect(report.overflowX, 'horizontal overflow').toBeNull();
          expect(report.smallTargets, 'interactive elements smaller than 32x32 px').toEqual([]);
          expect(report.clipped, 'clipped text without a title tooltip').toEqual([]);
        });
      }
      test('geometry: OpenRouter settings dialog', async ({ page }) => {
        await openSettings(page);
        const report = await page.evaluate(auditGeometry);
        expect(report.overflowX, 'horizontal overflow').toBeNull();
        expect(report.smallTargets, 'interactive elements smaller than 32x32 px').toEqual([]);
        expect(report.clipped, 'clipped text without a title tooltip').toEqual([]);
      });
    });
  }
});

// ================================================================================================ Law 5
/**
 * Clicks `trigger` while the matching API call is throttled and measures, in the page, the delay until a
 * busy state is visible (`[aria-busy="true"]`, `[role="progressbar"]` or `.animate-spin`) and the trigger is
 * disabled. A double click must send the request only once.
 */
async function expectImmediateFeedback(page: Page, triggerTestId: string, apiPattern: RegExp): Promise<void> {
  let requests = 0;
  await page.route(apiPattern, async (route) => {
    requests++;
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.evaluate((testId) => {
    const w = window as unknown as { __uxFeedback: { click: number; busy: number | null; disabled: number | null } };
    w.__uxFeedback = { click: 0, busy: null, disabled: null };
    const trigger = document.querySelector(`[data-testid="${testId}"]`) as HTMLButtonElement | null;
    const check = () => {
      const now = performance.now();
      const busy = document.querySelector('[aria-busy="true"], [role="progressbar"], .animate-spin');
      if (busy && w.__uxFeedback.busy === null) w.__uxFeedback.busy = now;
      const t = document.querySelector(`[data-testid="${testId}"]`) as HTMLButtonElement | null;
      if (t && (t.disabled || t.getAttribute('aria-disabled') === 'true') && w.__uxFeedback.disabled === null) {
        w.__uxFeedback.disabled = now;
      }
    };
    trigger?.addEventListener('click', () => (w.__uxFeedback.click = performance.now()), { capture: true, once: true });
    new MutationObserver(check).observe(document.body, { subtree: true, childList: true, attributes: true });
  }, triggerTestId);

  const trigger = page.getByTestId(triggerTestId);
  await expect(trigger).toBeEnabled();
  await trigger.dblclick();
  await page.waitForFunction(() => {
    const f = (window as unknown as { __uxFeedback: { busy: number | null; disabled: number | null } }).__uxFeedback;
    return f.busy !== null && f.disabled !== null;
  }, undefined, { timeout: 1000 });
  const f = await page.evaluate(() => (window as unknown as { __uxFeedback: { click: number; busy: number; disabled: number } }).__uxFeedback);
  expect(f.busy - f.click, 'busy indicator latency (ms)').toBeLessThan(100);
  expect(f.disabled - f.click, 'trigger disabled latency (ms)').toBeLessThan(100);
  await expect(trigger).toBeDisabled();
  await expect(page.locator('[aria-busy="true"]').first()).toBeVisible();
  // Double click guard: exactly one request in flight
  await page.waitForTimeout(200);
  expect(requests, 'requests sent by a double click').toBe(1);
  // ...and the UI recovers once the response arrives
  await expect(trigger).toBeEnabled({ timeout: 5000 });
}

test.describe('Law 5 - immediate visual feedback (< 100 ms) under a throttled network', () => {
  test('"Caption generieren" (re-caption the active image)', async ({ page }) => {
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await expectImmediateFeedback(page, 'recaption-btn', /\/api\/v1\/items\/\d+\/recaption$/);
  });

  test('"Smart-Crop" (apply the watermark crop proposal)', async ({ page }) => {
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await expectImmediateFeedback(page, 'smart-crop-toggle', /\/api\/v1\/items\/\d+\/smart-crop$/);
  });

  test('"Run OpenRouter cascade" (caption the whole dataset)', async ({ page }) => {
    await openView(page, '#/studio/captioning', '[data-testid="view-captioning"]');
    await expectImmediateFeedback(page, 'run-cascade-btn', /\/api\/v1\/datasets\/[^/]+\/caption$/);
  });
});
