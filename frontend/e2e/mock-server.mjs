#!/usr/bin/env node
/**
 * UX-gate server: serves the production build (src/web/dist/browser) plus a deterministic in-memory mock of
 * the FastAPI backend, so Playwright (frontend/e2e/ux-gate.spec.ts) and Lighthouse CI (frontend/lighthouserc.json)
 * run without Python, a database or real footage.
 *
 *   UX_GATE_PORT   port (default 4300)
 *   UX_GATE_DIST   build directory (default <repo>/src/web/dist/browser)
 *
 * `POST /__reset` restores the initial fixture state (called by the Playwright suite before each test).
 */
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import {
  DATASET_ID,
  OPENROUTER_MODELS,
  VIDEO_ID,
  makeRejections,
  makeStratification,
  makeStudioDatasets,
  makeStudioItems,
  makeStudioSettings,
  makeStudioSkills,
  makeCaptionTiers,
  makeChunks,
  makeCuration,
  makeDataset,
  makeFilmstrip,
  makeSettings,
  makeVideos,
} from './fixtures.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..');
const PORT = Number(process.env.UX_GATE_PORT || 4300);
const DIST = resolve(process.env.UX_GATE_DIST || join(REPO_ROOT, 'src', 'web', 'dist', 'browser'));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
};
const COMPRESSIBLE = new Set(['.html', '.js', '.mjs', '.css', '.json', '.svg', '.txt']);

// ----------------------------------------------------------------------------------------------- state
let state;
function resetState() {
  const curation = makeCuration();
  const tiers = new Map();
  for (const f of curation.picks) tiers.set(f.id, makeCaptionTiers(f.id, f.caption_text ?? ''));
  state = {
    videos: makeVideos(),
    curation,
    settings: makeSettings(),
    tiers,
    crops: new Map(),
    jobSeq: 1,
    // /api/v1 Studio
    datasets: makeStudioDatasets(),
    items: makeStudioItems(),
    studioSettings: makeStudioSettings(),
    skills: makeStudioSkills(),
  };
}

function findItem(mediaId) {
  return state.items.find((it) => it.id === mediaId) ?? null;
}

/** Smart-crop proposal for a Studio item: drop the bottom watermark strip, keep a 16:9-ish bucket. */
function studioSmartCrop(item) {
  return { x: 0, y: 0, width: item.width, height: Math.round(item.height * 0.88) };
}

function filterItems(query) {
  let items = state.items;
  const cs = query.get('curation_state');
  const cap = query.get('caption_status');
  const minQ = Number(query.get('min_quality') || 0);
  if (cs) items = items.filter((it) => it.curation_state === cs);
  if (cap) items = items.filter((it) => it.caption_status === cap);
  if (minQ) items = items.filter((it) => it.quality_score >= minQ);
  return items;
}
resetState();

function findFrame(frameId) {
  return state.curation.candidates.find((f) => f.id === frameId) ?? null;
}

/** Smart-crop proposal: cut the watermark strip (bottom 12 %) and keep the 16:9 bucket. */
function smartCropFor(frameId) {
  return { frame_id: frameId, x: 0, y: 0, width: 1, height: 0.88, target_bucket: '1536x864', reason: 'watermark' };
}

// ----------------------------------------------------------------------------------------------- images
function frameSvg(id, w, h) {
  const hue = (Number(id) * 47) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="hsl(${hue},45%,38%)"/><stop offset="1" stop-color="hsl(${(hue + 60) % 360},40%,22%)"/>
</linearGradient></defs>
<rect width="${w}" height="${h}" fill="url(#g)"/>
<circle cx="${w * 0.42}" cy="${h * 0.45}" r="${h * 0.22}" fill="hsl(${hue},30%,70%)" opacity="0.55"/>
<rect x="${w * 0.78}" y="${h * 0.9}" width="${w * 0.2}" height="${h * 0.07}" fill="white" opacity="0.35"/>
</svg>`;
}

// ----------------------------------------------------------------------------------------------- api
const json = (body, status = 200) => ({ status, body });

const routes = [
  ['GET', /^\/api\/videos$/, () => json({ videos: state.videos })],
  ['GET', /^\/api\/videos\/([^/]+)$/, (m) => {
    const video = state.videos.find((v) => v.id === decodeURIComponent(m[1]));
    return video ? json({ video }) : json({ detail: 'Video not found' }, 404);
  }],
  ['PATCH', /^\/api\/videos\/([^/]+)$/, (m, body) => {
    const video = state.videos.find((v) => v.id === decodeURIComponent(m[1]));
    if (!video) return json({ detail: 'Video not found' }, 404);
    Object.assign(video, body ?? {});
    return json({ video });
  }],
  ['GET', /^\/api\/videos\/[^/]+\/chunks$/, () => json({ chunks: makeChunks() })],
  ['PUT', /^\/api\/videos\/[^/]+\/chunks$/, (_m, body) => json({ chunks: body?.chunks ?? makeChunks() })],
  ['GET', /^\/api\/videos\/[^/]+\/filmstrip$/, () => json(makeFilmstrip())],
  ['GET', /^\/api\/videos\/[^/]+\/curation-300$/, (m) =>
    decodeURIComponent(m[0].split('/')[3]) === VIDEO_ID
      ? json(state.curation)
      : json({ picks: [], runners: [], candidates: [], chunks: [], distribution: null })],
  ['GET', /^\/api\/videos\/[^/]+\/candidates$/, () => json({ candidates: state.curation.candidates })],
  ['POST', /^\/api\/videos\/[^/]+\/candidates\/(\d+)\/state$/, (m, body) => {
    const frame = findFrame(Number(m[1]));
    if (!frame) return json({ detail: 'Frame not found' }, 404);
    frame.state = body?.state ?? frame.state;
    return json({ frame_id: frame.id, state: frame.state });
  }],
  ['PATCH', /^\/api\/picks\/(\d+)$/, (m, body) => {
    const frame = findFrame(Number(m[1]));
    if (!frame) return json({ detail: 'Pick not found' }, 404);
    if (body?.state) frame.state = body.state;
    if (body?.crop !== undefined) state.crops.set(frame.id, body.crop);
    return json({ pick: { frame_id: frame.id, role: frame.role, state: frame.state, crop: state.crops.get(frame.id) ?? null } });
  }],
  ['GET', /^\/api\/videos\/[^/]+\/persons$/, (m) => json({ video_id: m[0].split('/')[3], positive: [], negative: [] })],
  ['GET', /^\/api\/videos\/[^/]+\/tags$/, () => json({ tags: [] })],
  ['GET', /^\/api\/videos\/([^/]+)\/autopilot$/, (m) => json({
    video_id: decodeURIComponent(m[1]), target_person_id: null, target_person_name: null, autopilot: null,
    chunks: [], metrics: {},
  })],
  ['POST', /^\/api\/videos\/[^/]+\/caption-openrouter$/, () => json({ job_id: state.jobSeq++, status: 'queued', mode: 'concept' })],
  ['GET', /^\/api\/settings$/, () => json({ settings: state.settings })],
  ['PUT', /^\/api\/settings$/, (_m, body) => {
    const { openrouter_api_key: key, ...rest } = body ?? {};
    Object.assign(state.settings, rest, key ? { openrouter_api_key_set: true } : {});
    return json({ settings: state.settings });
  }],
  ['GET', /^\/api\/openrouter\/models$/, () => json({ models: OPENROUTER_MODELS })],
  ['GET', /^\/api\/dataset$/, () => json(makeDataset())],
  ['POST', /^\/api\/export$/, () => json({ exported: 12, per_video_dir: '/data/finished/per_video/workshop-session-a', merged_dir: '/data/finished/merged' })],
  ['GET', /^\/api\/tags$/, () => json({ tags: [], categories: [] })],
  ['GET', /^\/api\/persons$/, () => json({ persons: [], total: 0 })],
  ['GET', /^\/api\/scoring\/learned-weights$/, () => json({ weights: null, default_weights: {}, n_events: 0, accuracy: null })],
  ['GET', /^\/api\/rag\/status$/, () => json({ indexed: 0, total_captions: 0, stale: false, building: false })],
  ['GET', /^\/api\/rag\/presets$/, () => json({ presets: [] })],
  ['GET', /^\/api\/rag\/history/, () => json({ entries: [], total: 0 })],
  ['GET', /^\/api\/frames\/(\d+)\/caption$/, (m) => {
    const frame = findFrame(Number(m[1]));
    const text = frame?.caption_text;
    return json({ frame_id: Number(m[1]), caption: text ? { frame_id: Number(m[1]), video_id: VIDEO_ID, text, model: 'google/gemma-4-31b-it', layer: 'layer1', source: 'vlm', warnings: [] } : null });
  }],
  ['GET', /^\/api\/frames\/(\d+)\/tags$/, (m) => json({ frame_id: Number(m[1]), tags: [], regions: [], suggestions: [] })],
  ['GET', /^\/api\/frames\/(\d+)\/faces$/, (m) => json({ frame_id: Number(m[1]), faces: [] })],
  // MOSS 3-tier caption editor + smart crop
  ['GET', /^\/api\/frames\/(\d+)\/caption-tiers$/, (m) => {
    const id = Number(m[1]);
    return json(state.tiers.get(id) ?? makeCaptionTiers(id, ''));
  }],
  ['PUT', /^\/api\/frames\/(\d+)\/caption-tiers$/, (m, body) => {
    const id = Number(m[1]);
    const next = { ...(state.tiers.get(id) ?? makeCaptionTiers(id, '')), ...body, frame_id: id, source: 'human' };
    state.tiers.set(id, next);
    return json(next);
  }],
  ['POST', /^\/api\/frames\/(\d+)\/caption-tiers\/generate$/, (m) => {
    const id = Number(m[1]);
    const frame = findFrame(id);
    const next = makeCaptionTiers(id, frame?.caption_text || 'A person works at a bench in a bright workshop.');
    state.tiers.set(id, next);
    return json(next);
  }],
  ['GET', /^\/api\/frames\/(\d+)\/smart-crop$/, (m) => json(smartCropFor(Number(m[1])))],
  ['POST', /^\/api\/frames\/(\d+)\/smart-crop$/, (m) => json(smartCropFor(Number(m[1])))],
];


// ----------------------------------------------------------------------------------------------- /api/v1 (Studio)
const v1Routes = [
  ['GET', /^\/api\/v1\/health$/, () => json({ status: 'ok', sqlite_wal: true, ffmpeg: true, fts5: true, mounts: { input: true, exports: true, appdata: true } })],
  ['GET', /^\/api\/v1\/settings$/, () => json({ settings: state.studioSettings })],
  ['PUT', /^\/api\/v1\/settings$/, (_m, body) => {
    const { openrouter_api_key: key, ...rest } = body ?? {};
    Object.assign(state.studioSettings, rest, key ? { openrouter_api_key_set: true, openrouter_api_key_masked: 'sk-or-v1-****' + String(key).slice(-4) } : {});
    return json({ settings: state.studioSettings, ignored_keys: [] });
  }],
  ['POST', /^\/api\/v1\/settings\/test-openrouter$/, () => json({ valid: true, label: 'ux-gate key', usage_usd: 1.23, limit_usd: 20 })],
  ['GET', /^\/api\/v1\/fs\/browse$/, () => json({ current_path: '/input', directories: ['workshop'], videos: ['workshop-session-a.mp4'], image_counts: 0 })],
  ['GET', /^\/api\/v1\/datasets$/, () => json({ datasets: state.datasets })],
  ['GET', /^\/api\/v1\/datasets\/([^/]+)$/, () => json({ dataset: state.datasets[0] })],
  ['GET', /^\/api\/v1\/datasets\/[^/]+\/items$/, (_m, _b, q) => {
    const items = filterItems(q);
    return json({ items, total: items.length });
  }],
  ['POST', /^\/api\/v1\/datasets\/[^/]+\/items\/batch-triage$/, (_m, body) => {
    const ids = new Set(body?.media_ids ?? []);
    for (const it of state.items) if (ids.has(it.id)) it.curation_state = body.curation_state;
    return json({ updated: ids.size, curation_state: body?.curation_state });
  }],
  ['GET', /^\/api\/v1\/datasets\/[^/]+\/stratification$/, () => json(makeStratification())],
  ['GET', /^\/api\/v1\/datasets\/[^/]+\/rejections$/, () => json(makeRejections())],
  ['POST', /^\/api\/v1\/datasets\/[^/]+\/caption$/, () => json({ job_id: state.jobSeq++, status: 'completed', items_to_caption: 0, summary: { total: 0 } })],
  ['GET', /^\/api\/v1\/skills$/, () => json({ skills: state.skills })],
  ['PUT', /^\/api\/v1\/items\/(\d+)\/caption$/, (m, body) => {
    const item = findItem(Number(m[1]));
    if (!item) return json({ detail: 'Media item not found' }, 404);
    item.active_caption = body?.caption_text ?? item.active_caption;
    if (body?.caption_medium !== undefined) item.caption_medium = body.caption_medium;
    if (body?.caption_short !== undefined) item.caption_short = body.caption_short;
    item.caption_status = 'manual';
    item.active_caption_layer = 'manual';
    return json(item);
  }],
  ['POST', /^\/api\/v1\/items\/(\d+)\/approve$/, (m) => {
    const item = findItem(Number(m[1]));
    if (!item) return json({ detail: 'Media item not found' }, 404);
    item.caption_status = 'approved';
    return json(item);
  }],
  ['POST', /^\/api\/v1\/items\/(\d+)\/smart-crop$/, (m) => {
    const item = findItem(Number(m[1]));
    if (!item) return json({ detail: 'Media item not found' }, 404);
    item.crop_geometry = studioSmartCrop(item);
    return json(item);
  }],
  ['DELETE', /^\/api\/v1\/items\/(\d+)\/smart-crop$/, (m) => {
    const item = findItem(Number(m[1]));
    if (!item) return json({ detail: 'Media item not found' }, 404);
    item.crop_geometry = null;
    return json(item);
  }],
  ['POST', /^\/api\/v1\/items\/(\d+)\/recaption$/, (m) => {
    const item = findItem(Number(m[1]));
    if (!item) return json({ detail: 'Media item not found' }, 404);
    item.caption_status = 'vlm_accepted';
    item.active_caption_layer = 'primary';
    item.active_caption = item.active_caption || 'ohwx_subject, a person at a workbench in a bright workshop.';
    return json(item);
  }],
];

function handleApi(method, pathname, body, query) {
  for (const [verb, re, fn] of [...v1Routes, ...routes]) {
    if (verb !== method) continue;
    const m = pathname.match(re);
    if (m) return fn(m, body, query);
  }
  if (method === 'GET') {
    process.stderr.write(`[mock] unhandled GET ${pathname}\n`);
    return json({});
  }
  return json({ ok: true });
}

// ----------------------------------------------------------------------------------------------- http
function send(req, res, status, type, payload, extraHeaders = {}) {
  let data = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  const headers = { 'Content-Type': type, ...extraHeaders };
  if (/\bgzip\b/.test(req.headers['accept-encoding'] || '') && data.length > 1024 && COMPRESSIBLE.has(extFor(type))) {
    data = gzipSync(data);
    headers['Content-Encoding'] = 'gzip';
    headers['Vary'] = 'Accept-Encoding';
  }
  headers['Content-Length'] = data.length;
  res.writeHead(status, headers);
  res.end(req.method === 'HEAD' ? undefined : data);
}

function extFor(type) {
  return Object.entries(MIME).find(([, t]) => t === type)?.[0] ?? '';
}

async function serveStatic(req, res, pathname) {
  const rel = normalize(decodeURIComponent(pathname)).replace(/^(\.\.[/\\])+/, '');
  let file = join(DIST, rel);
  if (!file.startsWith(DIST)) file = join(DIST, 'index.html');
  try {
    const st = await stat(file);
    if (st.isDirectory()) file = join(file, 'index.html');
  } catch {
    file = join(DIST, 'index.html');
  }
  try {
    const data = await readFile(file);
    const ext = extname(file);
    const hashed = /-[A-Z0-9]{8}\.(js|css)$/.test(file);
    send(req, res, 200, MIME[ext] || 'application/octet-stream', data, {
      'Cache-Control': hashed ? 'public, max-age=31536000, immutable' : 'no-cache',
    });
  } catch {
    send(req, res, 500, MIME['.txt'], `Build output missing in ${DIST} - run: npx ng build`);
  }
}

function readBody(req) {
  return new Promise((ok) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      try {
        ok(raw ? JSON.parse(raw) : null);
      } catch {
        ok(null);
      }
    });
  });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const { pathname } = url;

  if (req.method === 'POST' && pathname === '/__reset') {
    resetState();
    return send(req, res, 200, MIME['.json'], '{"ok":true}');
  }
  if (pathname === '/api/events' || pathname === '/api/v1/events/stream') {
    // One "connected" event, then close; the long retry keeps EventSource from reconnecting during a run
    // (an open stream would keep Lighthouse from ever reaching network-idle).
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    return res.end('retry: 3600000\ndata: {"type":"connected"}\n\n');
  }
  const img = pathname.match(/^\/api\/(?:frames|videos\/[^/]+\/frames|v1\/media)\/(\d+)\/(preview|full|thumb|thumbnail)$/);
  if (img) {
    const [w, h] = img[2] === 'full' ? [1920, 1080] : [640, 360];
    return send(req, res, 200, MIME['.svg'], frameSvg(img[1], w, h), { 'Cache-Control': 'public, max-age=3600' });
  }
  if (pathname.startsWith('/api/')) {
    const body = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method) ? await readBody(req) : null;
    const out = handleApi(req.method, pathname, body, url.searchParams);
    return send(req, res, out.status, MIME['.json'], JSON.stringify(out.body), { 'Cache-Control': 'no-store' });
  }
  return serveStatic(req, res, pathname);
});

/** Starts the server; resolves once it listens. Used directly (CLI) and by lhci-server.mjs. */
export function startMockServer(port = PORT) {
  return new Promise((ok) => {
    server.listen(port, '127.0.0.1', () => {
      process.stdout.write(`[ux-gate] mock server on http://127.0.0.1:${port} serving ${DIST}\n`);
      ok(server);
    });
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await startMockServer();
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => server.close(() => process.exit(0)));
}
