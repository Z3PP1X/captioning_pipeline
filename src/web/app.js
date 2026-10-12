// Captioning Pipeline Full Interactive Application
let state = {
  videos: [],
  currentVideo: null,
  filmstrip: { thumbnails: [], cuts: [] },
  chunks: [],
  candidates: [],
  curation: { picks: [], runners: [], distribution: {}, target_quotas: {} },
  showRunnerUps: false,
  dataset: { total_exports: 0, per_video: [] },
  settings: {},
  inPoint: null,
  outPoint: null,
  snapCuts: true,
  currentTab: 'studio',
  activeJobs: new Map(),
  lightboxIdx: null,
  curateChunkFilter: null,
};

// ----------------------------------------------------------------------------- Formatters
function formatTime(s) {
  if (isNaN(s) || s === null || s === undefined) return "00:00:00.00";
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  const ms = Math.floor((s % 1) * 100);
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
}

function formatShortTime(s) {
  if (isNaN(s) || s === null) return "--:--";
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  if (hrs > 0) {
    return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function parseTimestamp(str) {
  if (!str) return null;
  str = String(str).trim();
  if (!str) return null;

  // Handle format like "90s", "90", "12.5"
  if (/^\d+(\.\d+)?s?$/i.test(str)) {
    return Math.max(0, parseFloat(str.replace(/s$/i, '')));
  }

  // Handle MM:SS, HH:MM:SS, or MM:SS.ms, HH:MM:SS.ms
  const parts = str.split(':');
  if (parts.length > 3) return null;
  const numParts = parts.map(p => parseFloat(p));
  if (numParts.some(isNaN)) return null;

  if (numParts.length === 3) {
    return Math.max(0, numParts[0] * 3600 + numParts[1] * 60 + numParts[2]);
  } else if (numParts.length === 2) {
    return Math.max(0, numParts[0] * 60 + numParts[1]);
  } else if (numParts.length === 1) {
    return Math.max(0, numParts[0]);
  }
  return null;
}

function jumpToTime(t) {
  if (t === null || isNaN(t)) return;
  const maxDur = videoEl.duration || state.currentVideo?.duration || 0;
  const clamped = Math.max(0, Math.min(maxDur, t));
  videoEl.currentTime = clamped;
}

// ----------------------------------------------------------------------------- API
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'API error');
  }
  return res.json();
}

// ----------------------------------------------------------------------------- SSE & Multi-Job Tracking
function initSSE() {
  const eventSource = new EventSource('/api/events');

  eventSource.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'job_queued' || msg.type === 'job_started' || msg.type === 'job_progress') {
        const d = msg.data;
        state.activeJobs.set(d.job_id, {
          job_id: d.job_id,
          video_id: d.video_id,
          video_name: d.video_name || 'Video',
          chunk_id: d.chunk_id,
          progress: d.progress || 0,
          step: d.step || 'Processing',
        });
        renderJobsContainer();
        updateVideoCardProgress();
      } else if (msg.type === 'job_finished') {
        const d = msg.data;
        state.activeJobs.delete(d.job_id);
        renderJobsContainer();
        loadVideos();

        if (state.currentVideo && state.currentVideo.id === d.video_id) {
          loadCandidates(d.video_id);
          showToast(
            'Extraction Complete',
            `Extracted candidates for ${d.video_name || 'current video'}. Ready for curation!`,
            d.video_id
          );
        } else {
          showToast(
            'Extraction Complete',
            `${d.video_name || 'Video'} has finished extracting candidates!`,
            d.video_id
          );
        }
      } else if (msg.type === 'job_cancelled' || msg.type === 'job_error') {
        const d = msg.data;
        state.activeJobs.delete(d.job_id);
        renderJobsContainer();
        loadVideos();
        if (msg.type === 'job_error') {
          showToast('Job Error', d.error || 'Extraction failed', null, true);
        } else {
          showToast('Job Cancelled', `Extraction cancelled for job #${d.job_id}`);
        }
      }
    } catch (err) {
      console.error('SSE parse error:', err);
    }
  };
}

function renderJobsContainer() {
  const container = document.getElementById('jobs-container');
  if (!container) return;

  if (state.activeJobs.size === 0) {
    container.innerHTML = '';
    return;
  }

  // Update or create job pills in place
  state.activeJobs.forEach(job => {
    let pill = container.querySelector(`[data-job-id="${job.job_id}"]`);
    const pct = Math.round((job.progress || 0) * 100);
    const shortName = job.video_name.length > 18 ? job.video_name.slice(0, 16) + '...' : job.video_name;

    if (!pill) {
      pill = document.createElement('div');
      pill.className = 'job-pill';
      pill.dataset.jobId = job.job_id;
      pill.title = `Job #${job.job_id} on ${job.video_name}. Click to switch.`;
      pill.innerHTML = `
        <span class="job-pill-spinner">⚡</span>
        <div class="job-pill-info">
          <span class="job-pill-name">${shortName}</span>
          <span class="job-pill-step">${job.step} (${pct}%)</span>
        </div>
        <div class="job-pill-bar">
          <div class="fill" style="width: ${pct}%"></div>
        </div>
        <button class="btn-job-cancel" title="Cancel this extraction job">✕</button>
      `;

      pill.onclick = (e) => {
        if (e.target.classList.contains('btn-job-cancel')) return;
        selectVideo(job.video_id);
        switchTab('curate');
      };

      pill.querySelector('.btn-job-cancel').onclick = async (e) => {
        e.stopPropagation();
        await api(`/api/jobs/${job.job_id}`, { method: 'DELETE' });
      };

      container.appendChild(pill);
    } else {
      const stepEl = pill.querySelector('.job-pill-step');
      if (stepEl) stepEl.textContent = `${job.step} (${pct}%)`;
      const fillEl = pill.querySelector('.job-pill-bar .fill');
      if (fillEl) fillEl.style.width = `${pct}%`;
    }
  });

  // Remove stale pills
  container.querySelectorAll('.job-pill').forEach(pill => {
    const jobId = parseInt(pill.dataset.jobId, 10);
    if (!state.activeJobs.has(jobId)) {
      pill.remove();
    }
  });
}

function showToast(title, message, videoId = null, isError = false) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast-card ${isError ? 'toast-error' : ''}`;
  toast.innerHTML = `
    <div class="toast-icon">${isError ? '⚠️' : '✓'}</div>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      <div class="toast-msg">${message}</div>
    </div>
    ${videoId ? '<button class="btn btn-small btn-toast-view">Curate Now</button>' : ''}
    <button class="toast-close">✕</button>
  `;

  if (videoId) {
    toast.querySelector('.btn-toast-view').onclick = () => {
      selectVideo(videoId);
      switchTab('curate');
      toast.remove();
    };
  }

  toast.querySelector('.toast-close').onclick = () => toast.remove();

  container.appendChild(toast);
  setTimeout(() => {
    if (toast.parentNode) toast.remove();
  }, 9000);
}

// ----------------------------------------------------------------------------- DOM Elements
const videoEl = document.getElementById('main-video');
const videoListEl = document.getElementById('video-list');
const chunksListEl = document.getElementById('chunks-list');
const timelineTrackEl = document.getElementById('timeline-track');
const filmstripLayerEl = document.getElementById('filmstrip-layer');
const cutsLayerEl = document.getElementById('cuts-layer');
const chunksLayerEl = document.getElementById('chunks-layer');
const playheadEl = document.getElementById('playhead');
const inOutRangeEl = document.getElementById('in-out-range');
const hoverPreviewEl = document.getElementById('timeline-hover-preview');
const hoverThumbEl = document.getElementById('hover-thumb');
const hoverTimeEl = document.getElementById('hover-time');
const curateGridEl = document.getElementById('curate-grid');

// ----------------------------------------------------------------------------- Initialize
document.addEventListener('DOMContentLoaded', async () => {
  setupEventListeners();
  initSSE();
  await loadSettings();
  await loadVideos();
  await selectNextVideo();
});

// ----------------------------------------------------------------------------- Tab Navigation
function switchTab(tabName) {
  state.currentTab = tabName;
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-view').forEach(v => {
    v.classList.toggle('active', v.id === `view-${tabName}`);
  });

  if (tabName === 'curate' && state.currentVideo) {
    loadCandidates(state.currentVideo.id);
  } else if (tabName === 'dataset') {
    loadDataset();
  }
}

// ----------------------------------------------------------------------------- Settings
async function loadSettings() {
  try {
    const data = await api('/api/settings');
    state.settings = data.settings;
    document.getElementById('setting-k').value = state.settings.default_k || 5;
    document.getElementById('setting-rule').value = state.settings.default_rule || 'person';
    document.getElementById('setting-fps').value = state.settings.sample_fps || 4.0;
    document.getElementById('setting-survivor').value = state.settings.survivor_frac || 0.5;
    document.getElementById('setting-trigger').value = state.settings.trigger_word || '';
    document.getElementById('setting-max-edge').value = state.settings.max_edge || 2048;
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

// ----------------------------------------------------------------------------- Videos
async function loadVideos() {
  try {
    const search = document.getElementById('video-search').value.trim();
    const status = document.getElementById('status-filter').value;
    const url = `/api/videos?${new URLSearchParams({ search, status })}`;
    const data = await api(url);
    state.videos = data.videos;
    renderVideoList();
  } catch (e) {
    console.error('Failed to load videos:', e);
  }
}

function renderVideoList() {
  document.getElementById('queue-count').textContent = `${state.videos.length} videos`;
  videoListEl.innerHTML = '';

  state.videos.forEach(v => {
    const card = document.createElement('div');
    card.dataset.videoId = v.id;
    const activeJob = Array.from(state.activeJobs.values()).find(j => j.video_id === v.id);
    const isExtracting = Boolean(activeJob);
    card.className = `video-card ${state.currentVideo?.id === v.id ? 'active' : ''} ${v.broken ? 'broken' : ''} ${isExtracting ? 'is-extracting' : ''}`;
    card.onclick = () => selectVideo(v.id);

    const statusBadgeClass = v.broken ? 'broken' : (isExtracting ? 'extracting' : (v.status || 'new'));
    const statusText = v.broken ? (v.broken_reason || 'BROKEN') : (isExtracting ? 'EXTRACTING' : (v.status || 'NEW'));

    let progressHtml = '';
    if (activeJob) {
      const pct = Math.round((activeJob.progress || 0) * 100);
      progressHtml = `
        <div class="video-card-progress">
          <div class="bar"><div class="fill" style="width: ${pct}%"></div></div>
          <span>${activeJob.step || 'Extracting...'} (${pct}%)</span>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="video-card-top">
        <span class="video-name" title="${v.name}">${v.name}</span>
        <span class="status-badge ${statusBadgeClass}">${statusText}</span>
      </div>
      <div class="video-card-meta">
        <span>⏱️ ${formatShortTime(v.duration)}</span>
        <span>📐 ${v.width}x${v.height}</span>
        <span>🎞️ ${v.chunk_count || 0} chunks</span>
        <span>📦 ${v.export_count || 0} exp</span>
      </div>
      ${progressHtml}
    `;
    videoListEl.appendChild(card);
  });
}

function updateVideoCardProgress() {
  state.videos.forEach(v => {
    const card = videoListEl.querySelector(`[data-video-id="${v.id}"]`);
    if (!card) return;
    const activeJob = Array.from(state.activeJobs.values()).find(j => j.video_id === v.id);
    const isExtracting = Boolean(activeJob);
    card.classList.toggle('is-extracting', isExtracting);

    let progEl = card.querySelector('.video-card-progress');
    const badgeEl = card.querySelector('.status-badge');
    if (activeJob) {
      const pct = Math.round((activeJob.progress || 0) * 100);
      if (badgeEl && !v.broken) {
        badgeEl.className = 'status-badge extracting';
        badgeEl.textContent = 'EXTRACTING';
      }
      if (!progEl) {
        progEl = document.createElement('div');
        progEl.className = 'video-card-progress';
        card.appendChild(progEl);
      }
      progEl.innerHTML = `
        <div class="bar"><div class="fill" style="width: ${pct}%"></div></div>
        <span>${activeJob.step || 'Extracting...'} (${pct}%)</span>
      `;
    } else {
      if (progEl) progEl.remove();
      if (badgeEl && !v.broken) {
        badgeEl.className = `status-badge ${v.status || 'new'}`;
        badgeEl.textContent = (v.status || 'NEW').toUpperCase();
      }
    }
  });
}

async function selectVideo(videoId) {
  const vid = state.videos.find(v => v.id === videoId);
  if (!vid) return;

  state.currentVideo = vid;
  renderVideoList();

  document.getElementById('current-video-title').textContent = vid.name;
  document.getElementById('curate-video-title').textContent = vid.name;
  document.getElementById('current-video-meta').innerHTML = `
    <span class="meta-pill">⏱️ ${formatTime(vid.duration)}</span>
    <span class="meta-pill">📐 ${vid.width}x${vid.height} (${vid.fps} fps)</span>
    <span class="meta-pill">🎬 ${vid.codec}</span>
    <span class="meta-pill">💾 ${(vid.size / 1e6).toFixed(1)} MB</span>
  `;

  const statusSelect = document.getElementById('video-status-select');
  statusSelect.disabled = false;
  statusSelect.value = vid.status || 'new';

  state.inPoint = null;
  state.outPoint = null;
  updateInOutDisplay();

  videoEl.src = `/api/videos/${vid.id}/stream`;
  videoEl.load();

  await loadChunks(vid.id);
  loadFilmstrip(vid.id);

  if (state.currentTab === 'curate') {
    loadCandidates(vid.id);
  }
}

async function selectNextVideo() {
  try {
    const res = await api('/api/videos/next');
    if (res.video) {
      await selectVideo(res.video.id);
    } else if (state.videos.length > 0) {
      await selectVideo(state.videos[0].id);
    }
  } catch (e) {
    console.error('Error selecting next video:', e);
  }
}

// ----------------------------------------------------------------------------- Chunks
async function loadChunks(videoId) {
  try {
    const res = await api(`/api/videos/${videoId}/chunks`);
    state.chunks = res.chunks || [];
    renderChunksList();
    renderTimelineChunks();
  } catch (e) {
    console.error('Failed to load chunks:', e);
  }
}

function normalizeChunkBudgets() {
  if (!state.chunks || state.chunks.length === 0) return;
  const base = Math.floor(100 / state.chunks.length);
  let rem = 100 - (base * state.chunks.length);
  state.chunks.forEach((c, i) => {
    c.budget_pct = base + (i < rem ? 1 : 0);
  });
}

function updateBudgetSummary() {
  const sumBudget = state.chunks.reduce((acc, c) => acc + (parseFloat(c.budget_pct) || 0), 0);
  const rounded = Math.round(sumBudget);
  const sumEl = document.getElementById('budget-sum-val');
  const summaryBadge = document.getElementById('budget-summary');
  if (sumEl) sumEl.textContent = `${rounded}%`;
  if (summaryBadge) {
    if (Math.abs(sumBudget - 100) < 0.5) {
      summaryBadge.classList.remove('invalid');
      summaryBadge.title = 'Budget properly balanced to 100%';
    } else {
      summaryBadge.classList.add('invalid');
      summaryBadge.title = `Current total is ${rounded}%. Must sum to 100%`;
    }
  }
}

async function saveChunks(showAlert = true) {
  if (!state.currentVideo) return;
  if (state.chunks.length > 15) {
    alert('Maximum 15 chunks allowed. Please delete excess chunks.');
    return;
  }
  try {
    const res = await api(`/api/videos/${state.currentVideo.id}/chunks`, {
      method: 'PUT',
      body: JSON.stringify({ chunks: state.chunks }),
    });
    state.chunks = res.chunks;
    renderChunksList();
    renderTimelineChunks();
    loadVideos();
    if (showAlert) {
      showToast('Chunks Saved', 'Chunk boundaries and budget weights saved successfully!');
    }
  } catch (e) {
    alert('Error saving chunks: ' + e.message);
  }
}

function addChunkFromInOut() {
  if (!state.currentVideo) return;
  if (state.chunks.length >= 15) {
    alert('Maximum 15 scene chunks allowed per video.');
    return;
  }
  if (state.inPoint === null || state.outPoint === null) {
    alert('Please set both [ In and ] Out points first.');
    return;
  }
  let start = Math.min(state.inPoint, state.outPoint);
  let end = Math.max(state.inPoint, state.outPoint);
  if (end - start < 0.5) {
    alert('Chunk duration must be at least 0.5 seconds.');
    return;
  }

  const newChunk = {
    position: state.chunks.length,
    start_s: round(start, 2),
    end_s: round(end, 2),
    budget_pct: 0,
    k: state.settings.default_k || 5,
    rule: state.settings.default_rule || 'person',
    tags: [],
    notes: `Scene ${state.chunks.length + 1}`,
  };

  state.chunks.push(newChunk);
  state.chunks.sort((a, b) => a.start_s - b.start_s);
  state.chunks.forEach((c, idx) => c.position = idx);

  normalizeChunkBudgets();

  state.inPoint = null;
  state.outPoint = null;
  updateInOutDisplay();

  renderChunksList();
  renderTimelineChunks();
}

function renderChunksList() {
  const badgeEl = document.getElementById('chunk-count-badge');
  if (badgeEl) badgeEl.textContent = state.chunks.length;
  chunksListEl.innerHTML = '';

  if (state.chunks.length === 0) {
    chunksListEl.innerHTML = '<div class="empty-chunks-msg">No scene chunks defined yet. Mark [ In and ] Out points on the video or click Auto-Suggest.</div>';
    updateBudgetSummary();
    return;
  }

  // Ensure initial budgets are populated if zero
  const sumBudget = state.chunks.reduce((acc, c) => acc + (parseFloat(c.budget_pct) || 0), 0);
  if (sumBudget <= 0 && state.chunks.length > 0) {
    normalizeChunkBudgets();
  }

  state.chunks.forEach((chunk, idx) => {
    const card = document.createElement('div');
    card.className = 'chunk-card';
    const dur = (chunk.end_s - chunk.start_s).toFixed(1);

    card.innerHTML = `
      <div class="chunk-id-col">#${idx + 1}</div>
      <div class="chunk-time-edit-col">
        <input type="text" class="chunk-time-input chunk-start-input" value="${formatShortTime(chunk.start_s)}" title="Edit start time (MM:SS, HH:MM:SS, or seconds)">
        <span>→</span>
        <input type="text" class="chunk-time-input chunk-end-input" value="${formatShortTime(chunk.end_s)}" title="Edit end time (MM:SS, HH:MM:SS, or seconds)">
        <span class="chunk-dur">${dur}s</span>
      </div>
      <div class="chunk-budget-col">
        <input type="number" class="chunk-input-budget" value="${chunk.budget_pct !== undefined ? chunk.budget_pct : 0}" min="1" max="100" step="1" title="Frame Budget % of 100%">
        <span>%</span>
      </div>
      <div>
        <input type="number" class="chunk-input-k" value="${chunk.k || 5}" min="1" max="50" title="Target frame count K">
      </div>
      <div>
        <select class="chunk-select-rule">
          <option value="person" ${chunk.rule === 'person' ? 'selected' : ''}>Person</option>
          <option value="equipment" ${chunk.rule === 'equipment' ? 'selected' : ''}>Equipment</option>
          <option value="both" ${chunk.rule === 'both' ? 'selected' : ''}>Both</option>
          <option value="either" ${chunk.rule === 'either' ? 'selected' : ''}>Either</option>
          <option value="none" ${chunk.rule === 'none' ? 'selected' : ''}>None</option>
        </select>
      </div>
      <div>
        <input type="text" class="chunk-input-tags" value="${(chunk.tags || []).join(', ')}" placeholder="tags (rope, chair, etc.)">
      </div>
      <div class="chunk-actions-col">
        <button class="btn btn-small btn-chunk-extract" title="Extract only this chunk">⚡</button>
        <button class="btn btn-small btn-seek" title="Seek to start">▶</button>
        <button class="btn btn-small btn-delete" title="Delete chunk">✕</button>
      </div>
    `;

    const startInput = card.querySelector('.chunk-start-input');
    const endInput = card.querySelector('.chunk-end-input');

    const handleTimeChange = () => {
      const newStart = parseTimestamp(startInput.value);
      const newEnd = parseTimestamp(endInput.value);
      const maxDur = videoEl.duration || state.currentVideo?.duration || Infinity;

      if (newStart !== null && !isNaN(newStart) && newStart >= 0 && newStart <= maxDur) {
        chunk.start_s = round(newStart, 2);
      } else {
        startInput.value = formatShortTime(chunk.start_s);
      }

      if (newEnd !== null && !isNaN(newEnd) && newEnd >= 0 && newEnd <= maxDur) {
        chunk.end_s = round(newEnd, 2);
      } else {
        endInput.value = formatShortTime(chunk.end_s);
      }

      if (chunk.end_s <= chunk.start_s) {
        chunk.end_s = round(chunk.start_s + 1.0, 2);
        endInput.value = formatShortTime(chunk.end_s);
      }

      state.chunks.sort((a, b) => a.start_s - b.start_s);
      state.chunks.forEach((c, i) => c.position = i);
      renderChunksList();
      renderTimelineChunks();
    };

    startInput.onchange = handleTimeChange;
    endInput.onchange = handleTimeChange;
    startInput.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); handleTimeChange(); } };
    endInput.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); handleTimeChange(); } };

    const budgetInput = card.querySelector('.chunk-input-budget');
    budgetInput.onchange = (e) => {
      chunk.budget_pct = Math.max(0, Math.min(100, parseFloat(e.target.value) || 0));
      updateBudgetSummary();
    };

    const kInput = card.querySelector('.chunk-input-k');
    kInput.onchange = (e) => chunk.k = parseInt(e.target.value) || 5;

    const ruleSelect = card.querySelector('.chunk-select-rule');
    ruleSelect.onchange = (e) => chunk.rule = e.target.value;

    const tagsInput = card.querySelector('.chunk-input-tags');
    tagsInput.onchange = (e) => chunk.tags = e.target.value.split(',').map(s => s.trim()).filter(Boolean);

    card.querySelector('.btn-seek').onclick = () => { videoEl.currentTime = chunk.start_s; };
    card.querySelector('.btn-delete').onclick = () => {
      state.chunks.splice(idx, 1);
      normalizeChunkBudgets();
      renderChunksList();
      renderTimelineChunks();
    };

    card.querySelector('.btn-chunk-extract').onclick = async () => {
      if (!state.currentVideo) return;
      await saveChunks(false);
      const savedChunk = state.chunks[idx];
      if (!savedChunk || !savedChunk.id) {
        alert('Could not resolve saved chunk ID.');
        return;
      }
      try {
        const res = await api('/api/jobs', {
          method: 'POST',
          body: JSON.stringify({ video_id: state.currentVideo.id, chunk_id: savedChunk.id }),
        });
        showToast(
          'Extraction Started',
          `Extracting Chunk #${idx + 1} for ${state.currentVideo.name} in background...`,
          state.currentVideo.id
        );
      } catch (e) {
        alert('Error extracting chunk: ' + e.message);
      }
    };

    chunksListEl.appendChild(card);
  });

  updateBudgetSummary();
}

// ----------------------------------------------------------------------------- Filmstrip & Timeline
async function loadFilmstrip(videoId) {
  filmstripLayerEl.innerHTML = '';
  cutsLayerEl.innerHTML = '';
  state.filmstrip = { thumbnails: [], cuts: [] };

  try {
    const data = await api(`/api/videos/${videoId}/filmstrip`);
    state.filmstrip = data;

    filmstripLayerEl.innerHTML = '';
    (data.thumbnails || []).forEach(th => {
      const img = document.createElement('img');
      img.className = 'filmstrip-thumb';
      img.src = th.url;
      filmstripLayerEl.appendChild(img);
    });

    cutsLayerEl.innerHTML = '';
    const dur = state.currentVideo?.duration || 1;
    (data.cuts || []).forEach(t => {
      const marker = document.createElement('div');
      marker.className = 'cut-marker';
      marker.style.left = `${(t / dur) * 100}%`;
      marker.title = `Scene Cut at ${formatTime(t)}`;
      cutsLayerEl.appendChild(marker);
    });
  } catch (e) {
    console.error('Error loading filmstrip:', e);
  }
}

function renderTimelineChunks() {
  chunksLayerEl.innerHTML = '';
  const dur = state.currentVideo?.duration || 1;

  state.chunks.forEach((ch, idx) => {
    const leftPct = (ch.start_s / dur) * 100;
    const widthPct = ((ch.end_s - ch.start_s) / dur) * 100;

    const band = document.createElement('div');
    band.className = 'chunk-band';
    band.style.left = `${leftPct}%`;
    band.style.width = `${widthPct}%`;
    band.textContent = `#${idx + 1}`;
    band.title = `Chunk #${idx + 1} (${ch.start_s.toFixed(1)}s - ${ch.end_s.toFixed(1)}s)`;

    band.onclick = (e) => {
      e.stopPropagation();
      videoEl.currentTime = ch.start_s;
    };

    chunksLayerEl.appendChild(band);
  });
}

function updateInOutDisplay() {
  document.getElementById('tc-in').textContent = state.inPoint !== null ? formatShortTime(state.inPoint) : '--:--';
  document.getElementById('tc-out').textContent = state.outPoint !== null ? formatShortTime(state.outPoint) : '--:--';

  const dur = state.currentVideo?.duration || 1;
  if (state.inPoint !== null && state.outPoint !== null) {
    const start = Math.min(state.inPoint, state.outPoint);
    const end = Math.max(state.inPoint, state.outPoint);
    inOutRangeEl.style.display = 'block';
    inOutRangeEl.style.left = `${(start / dur) * 100}%`;
    inOutRangeEl.style.width = `${((end - start) / dur) * 100}%`;
  } else if (state.inPoint !== null) {
    inOutRangeEl.style.display = 'block';
    inOutRangeEl.style.left = `${(state.inPoint / dur) * 100}%`;
    inOutRangeEl.style.width = '2px';
  } else {
    inOutRangeEl.style.display = 'none';
  }
}

function snapToNearestCut(t) {
  if (!state.snapCuts || !state.filmstrip.cuts || state.filmstrip.cuts.length === 0) return t;
  const window_s = state.settings.snap_window_s || 1.5;
  let closest = t;
  let minDiff = window_s;
  state.filmstrip.cuts.forEach(cut => {
    const diff = Math.abs(cut - t);
    if (diff < minDiff) {
      minDiff = diff;
      closest = cut;
    }
  });
  return closest;
}

// ----------------------------------------------------------------------------- Curation Grid (Phase 3: 300 Best Frames)
async function loadCandidates(videoId) {
  try {
    const res = await api(`/api/videos/${videoId}/curation-300`);
    state.curation = res;
    state.candidates = [...(res.picks || []), ...(res.runners || [])];
    updateDistributionBar(res.distribution);
    updateCurateChunkFilterOptions();
    renderCurationGrid();
  } catch (e) {
    console.warn('Failed to load curation-300, falling back to /candidates:', e);
    try {
      const fallback = await api(`/api/videos/${videoId}/candidates`);
      state.candidates = fallback.frames || [];
      state.curation = { picks: state.candidates.filter(c => c.role === 'pick'), runners: state.candidates.filter(c => c.role === 'runner_up') };
      updateDistributionBar();
      updateCurateChunkFilterOptions();
      renderCurationGrid();
    } catch (err) {
      console.error('Failed to load candidates:', err);
    }
  }
}

function updateDistributionBar(dist) {
  if (!dist) {
    const picks = (state.candidates || []).filter(c => c.role === 'pick' && c.state !== 'reject');
    const N = picks.length || 1;
    let mCount = 0, sCount = 0, tCount = 0, fCount = 0;
    picks.forEach(c => {
      const met = c.metrics || {};
      if (met.multiple_persons) mCount++;
      if (met.one_person) sCount++;
      if (met.tools || met.equipment) tCount++;
      if (met.face_visible_50pct || (met.face_score && met.face_score >= 0.5)) fCount++;
    });
    dist = {
      multiple_pct: round((mCount / N) * 100, 1),
      solo_pct: round((sCount / N) * 100, 1),
      tools_pct: round((tCount / N) * 100, 1),
      face_pct: round((fCount / N) * 100, 1),
      total_picks: picks.length,
    };
  }

  const setMeter = (valId, fillId, val, targetMin, targetMax) => {
    const vEl = document.getElementById(valId);
    const fEl = document.getElementById(fillId);
    if (vEl) vEl.textContent = `${Math.round(val)}%`;
    if (fEl) {
      fEl.style.width = `${Math.min(100, val)}%`;
      if (targetMin !== undefined && (val < targetMin || (targetMax && val > targetMax))) {
        fEl.classList.add('warning');
      } else {
        fEl.classList.remove('warning');
      }
    }
  };

  setMeter('val-multiple', 'fill-multiple', dist.multiple_pct, 45, 55);
  setMeter('val-solo', 'fill-solo', dist.solo_pct, 20, 35);
  setMeter('val-tools', 'fill-tools', dist.tools_pct, 15, 30);
  setMeter('val-face', 'fill-face', dist.face_pct, 20);

  const countEl = document.getElementById('val-selected-count');
  if (countEl) {
    const pickCount = (state.candidates || []).filter(c => c.role === 'pick' && c.state !== 'reject').length;
    countEl.textContent = `${pickCount} / 300`;
  }
}

function updateCurateChunkFilterOptions() {
  const select = document.getElementById('curate-chunk-filter');
  if (!select) return;

  const currentVal = select.value;
  select.innerHTML = `<option value="all">All Chunks (${state.candidates.length} candidates)</option>`;

  const chunkMap = new Map();
  (state.chunks || []).forEach((ch, idx) => {
    chunkMap.set(ch.id, {
      id: ch.id,
      position: idx,
      start: ch.start_s,
      end: ch.end_s,
      tags: ch.tags || [],
      count: 0,
      picks: 0,
    });
  });

  state.candidates.forEach(cand => {
    let entry = chunkMap.get(cand.chunk_id);
    if (!entry) {
      entry = {
        id: cand.chunk_id,
        position: cand.chunk_position ?? 0,
        start: cand.chunk_start ?? 0,
        end: cand.chunk_end ?? 0,
        tags: cand.chunk_tags || [],
        count: 0,
        picks: 0,
      };
      chunkMap.set(cand.chunk_id, entry);
    }
    entry.count++;
    if (cand.role === 'pick' && cand.state !== 'reject') entry.picks++;
  });

  Array.from(chunkMap.values())
    .sort((a, b) => a.position - b.position)
    .forEach(entry => {
      const opt = document.createElement('option');
      opt.value = String(entry.id);
      const tagStr = entry.tags && entry.tags.length ? ` [${entry.tags.join(', ')}]` : '';
      opt.textContent = `Chunk #${entry.position + 1} (${formatShortTime(entry.start)} → ${formatShortTime(entry.end)})${tagStr} • ${entry.picks} picks / ${entry.count} total`;
      select.appendChild(opt);
    });

  if (state.curateChunkFilter) {
    select.value = state.curateChunkFilter;
    state.curateChunkFilter = null;
  } else if (currentVal && Array.from(select.options).some(o => o.value === currentVal)) {
    select.value = currentVal;
  }
}

function renderCurationGrid() {
  const chunkFilter = document.getElementById('curate-chunk-filter')?.value || 'all';
  const roleFilter = document.getElementById('curate-role-filter')?.value || 'all';
  const stateFilter = document.getElementById('curate-state-filter')?.value || 'all';

  const filtered = state.candidates.filter(c => {
    if (chunkFilter !== 'all' && String(c.chunk_id) !== chunkFilter) return false;
    if (roleFilter !== 'all') {
      if (c.role !== roleFilter) return false;
    } else if (!state.showRunnerUps && c.role === 'runner_up') {
      return false;
    }
    if (stateFilter !== 'all' && c.state !== stateFilter) return false;
    return true;
  });

  const picksCount = state.candidates.filter(c => c.role === 'pick' && c.state !== 'reject').length;
  const totalCands = state.candidates.length;
  const statsBadge = document.getElementById('curate-stats-badge');
  if (statsBadge) {
    statsBadge.textContent = `${picksCount} / 300 picks (${totalCands} pool)`;
  }
  curateGridEl.innerHTML = '';

  if (filtered.length === 0) {
    curateGridEl.innerHTML = '<div class="empty-chunks-msg">No candidates match the filter.</div>';
    return;
  }

  // Group candidates by chunk_id
  const groups = new Map();
  filtered.forEach(c => {
    const cid = c.chunk_id;
    if (!groups.has(cid)) groups.set(cid, []);
    groups.get(cid).push(c);
  });

  const sortedChunkIds = Array.from(groups.keys()).sort((a, b) => {
    const ca = groups.get(a)[0];
    const cb = groups.get(b)[0];
    return (ca.chunk_position ?? 0) - (cb.chunk_position ?? 0);
  });

  sortedChunkIds.forEach(chunkId => {
    const chunkCands = groups.get(chunkId);
    const first = chunkCands[0];
    const chunkNum = (first.chunk_position ?? 0) + 1;
    const start = first.chunk_start ?? 0;
    const end = first.chunk_end ?? 0;
    const dur = (end - start).toFixed(1);
    const tags = Array.isArray(first.chunk_tags) ? first.chunk_tags.join(', ') : (first.chunk_tags || '');
    const picksInChunk = chunkCands.filter(c => c.role === 'pick' && c.state !== 'reject').length;

    const section = document.createElement('div');
    section.className = 'curate-chunk-section';

    section.innerHTML = `
      <div class="curate-chunk-header">
        <div class="chunk-header-title">
          <span class="chunk-header-badge">Chunk #${chunkNum}</span>
          <span class="chunk-header-time">${formatShortTime(start)} → ${formatShortTime(end)} (${dur}s)</span>
          ${tags ? `<span class="chunk-header-tags">${tags}</span>` : ''}
          <span class="chunk-header-count">${picksInChunk} picks</span>
        </div>
        <div class="chunk-header-actions">
          <button class="btn btn-small btn-keep-chunk" title="Keep all candidates in this chunk">✓ Keep All</button>
          <button class="btn btn-small btn-reject-chunk" title="Deselect all picks in this chunk">✕ Deselect All</button>
          <button class="btn btn-small btn-seek-chunk" title="Jump to chunk start in Studio">▶ Seek</button>
        </div>
      </div>
      <div class="curate-chunk-cards"></div>
    `;

    section.querySelector('.btn-keep-chunk').onclick = async () => {
      for (const cand of chunkCands) {
        if (cand.state !== 'keep' && cand.state !== 'star') {
          cand.state = 'keep';
          api(`/api/picks/${cand.id}`, { method: 'PATCH', body: JSON.stringify({ state: 'keep' }) });
        }
      }
      updateDistributionBar();
      renderCurationGrid();
    };

    section.querySelector('.btn-reject-chunk').onclick = async () => {
      for (const cand of chunkCands) {
        if (cand.role === 'pick') {
          await swapCandidate(cand, false);
        }
      }
      updateDistributionBar();
      renderCurationGrid();
    };

    section.querySelector('.btn-seek-chunk').onclick = () => {
      videoEl.currentTime = start;
      switchTab('studio');
    };

    const cardsContainer = section.querySelector('.curate-chunk-cards');
    chunkCands.forEach(cand => {
      const card = renderCandidateCard(cand);
      cardsContainer.appendChild(card);
    });

    curateGridEl.appendChild(section);
  });
}

function renderCandidateCard(cand) {
  const card = document.createElement('div');
  const isRunnerUp = cand.role === 'runner_up';
  card.className = `candidate-card state-${cand.state || 'keep'} ${isRunnerUp ? 'is-runner-up' : ''}`;
  const m = cand.metrics || {};
  const chunkPos = (cand.chunk_position ?? 0) + 1;

  // Build 7-dimension score pills
  const pills = [];
  pills.push(`<span class="metric-pill" title="Sharpness">🎯 ${Math.round(m.sharpness || m.tile_sharp || 0)}</span>`);
  pills.push(`<span class="metric-pill" title="Quality">✨ ${round(m.quality || 0, 2)}</span>`);
  if (m.multiple_persons) {
    pills.push(`<span class="metric-pill tag-multi" title="Multiple People">👥 Multi</span>`);
  } else if (m.one_person) {
    pills.push(`<span class="metric-pill tag-solo" title="One Person">👤 Solo</span>`);
  }
  if (m.tools || m.equipment) {
    pills.push(`<span class="metric-pill tag-tools" title="Tools & Equipment">⛓️ Tool/Eq</span>`);
  }
  if (m.face_visible_50pct || (m.face_score && m.face_score >= 0.5)) {
    pills.push(`<span class="metric-pill tag-face" title="Face ≥ 50%">👁️ Face ≥50%</span>`);
  }

  const roleBadgeText = isRunnerUp ? `RUNNER #${cand.rank + 1}` : `PICK #${cand.rank + 1}`;
  const roleBadgeClass = isRunnerUp ? 'runner_up' : 'pick';

  card.innerHTML = `
    <div class="card-thumb-wrap">
      <img src="${cand.preview_url}" alt="Candidate ${cand.id}" loading="lazy">
      <span class="card-role-badge ${roleBadgeClass}">C#${chunkPos} • ${roleBadgeText}</span>
      <span class="card-time-badge">${formatTime(cand.t)}</span>
    </div>
    <div class="card-body">
      <div class="card-metrics-row">
        ${pills.join('')}
      </div>
      <div class="card-actions-row">
        <div class="action-btn-group">
          ${isRunnerUp ? `
            <button class="btn btn-small btn-promote btn-accent" title="Promote to 300 Pick set">⬆ Promote</button>
          ` : `
            <button class="btn btn-small btn-keep ${cand.state === 'keep' ? 'btn-accent' : ''}" title="Keep (K)">✓ Keep</button>
            <button class="btn btn-small btn-swap btn-secondary" title="Deselect & Swap with next best candidate from chunk">✕ Swap</button>
            <button class="btn btn-small btn-star ${cand.state === 'star' ? 'btn-accent' : ''}" title="Star (S)">★</button>
          `}
        </div>
        <button class="btn btn-small btn-zoom" title="Inspect Fullscreen">🔍 1:1</button>
      </div>
    </div>
  `;

  card.querySelector('.card-thumb-wrap').onclick = () => openLightbox(cand);
  card.querySelector('.btn-zoom').onclick = () => openLightbox(cand);

  if (isRunnerUp) {
    card.querySelector('.btn-promote').onclick = async (e) => {
      e.stopPropagation();
      await promoteRunnerUp(cand);
    };
  } else {
    card.querySelector('.btn-keep').onclick = async (e) => {
      e.stopPropagation();
      await updatePickState(cand, 'keep');
    };
    card.querySelector('.btn-swap').onclick = async (e) => {
      e.stopPropagation();
      await swapCandidate(cand, true);
    };
    card.querySelector('.btn-star').onclick = async (e) => {
      e.stopPropagation();
      await updatePickState(cand, 'star');
    };
  }

  return card;
}

async function swapCandidate(cand, notify = true) {
  if (!state.currentVideo) return;
  try {
    const res = await api(`/api/videos/${state.currentVideo.id}/swap-frame`, {
      method: 'POST',
      body: JSON.stringify({ deselected_frame_id: cand.id }),
    });

    state.curation.picks = res.picks;
    state.curation.runners = res.runners;
    state.curation.distribution = res.distribution;
    state.candidates = [...res.picks, ...res.runners];

    updateDistributionBar(res.distribution);
    updateCurateChunkFilterOptions();
    renderCurationGrid();

    if (notify) {
      const inId = res.swapped_in?.id;
      showToast('Candidate Swapped', `Replaced frame #${cand.id} with candidate #${inId} from Chunk #${(cand.chunk_position ?? 0) + 1}`);
    }
  } catch (e) {
    console.error('Swap candidate error:', e);
    alert('Swap candidate failed: ' + e.message);
  }
}

async function promoteRunnerUp(cand) {
  // Promote runner up to pick, swap out lowest scoring pick from the same chunk
  const chunkPicks = state.candidates.filter(c => c.chunk_id === cand.chunk_id && c.role === 'pick');
  let lowestPick = chunkPicks[chunkPicks.length - 1];
  if (lowestPick) {
    await api(`/api/picks/${lowestPick.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ role: 'runner_up', state: 'reject' }),
    });
    lowestPick.role = 'runner_up';
    lowestPick.state = 'reject';
  }

  await api(`/api/picks/${cand.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ role: 'pick', state: 'keep' }),
  });
  cand.role = 'pick';
  cand.state = 'keep';

  updateDistributionBar();
  updateCurateChunkFilterOptions();
  renderCurationGrid();
  showToast('Candidate Promoted', `Frame #${cand.id} promoted to active 300 pick set.`);
}

async function updatePickState(cand, newState) {
  try {
    await api(`/api/picks/${cand.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ state: newState }),
    });
    cand.state = newState;
    updateCurateChunkFilterOptions();
    renderCurationGrid();
  } catch (e) {
    console.error('Failed to update pick:', e);
  }
}

// ----------------------------------------------------------------------------- Lightbox Inspector
function openLightbox(cand) {
  const modal = document.getElementById('modal-lightbox');
  const img = document.getElementById('lightbox-img');
  img.src = cand.full_url;

  document.getElementById('lightbox-time').textContent = formatTime(cand.t);
  document.getElementById('lightbox-badges').innerHTML = `
    <span class="status-badge ${cand.role}">${cand.role.toUpperCase()} #${cand.rank + 1}</span>
    <span class="status-badge ${cand.state}">${cand.state.toUpperCase()}</span>
  `;

  const m = cand.metrics || {};
  const details = {
    role: cand.role,
    rank_in_chunk: cand.rank !== undefined ? cand.rank + 1 : undefined,
    chunk_index: (cand.chunk_position ?? 0) + 1,
    timestamp: formatTime(cand.t),
    "7_dimension_scores": {
      sharpness: Math.round(m.sharpness || m.tile_sharp || 0),
      quality: round(m.quality || 0, 3),
      multiple_persons: Boolean(m.multiple_persons),
      one_person: Boolean(m.one_person),
      tools: Boolean(m.tools),
      equipment: Boolean(m.equipment),
      face_score: round(m.face_score || 0, 3),
      face_visible_50pct: Boolean(m.face_visible_50pct),
      composite: round(m.composite || cand.composite_score || 0, 3),
    },
    zero_shot_probs: cand.zs_scores || {},
  };
  document.getElementById('lightbox-metrics').textContent = JSON.stringify(details, null, 2);

  document.getElementById('lb-btn-keep').onclick = () => { updatePickState(cand, 'keep'); closeLightbox(); };
  document.getElementById('lb-btn-reject').onclick = () => {
    if (cand.role === 'pick') {
      swapCandidate(cand, true);
    } else {
      updatePickState(cand, 'reject');
    }
    closeLightbox();
  };
  document.getElementById('lb-btn-star').onclick = () => { updatePickState(cand, 'star'); closeLightbox(); };

  modal.style.display = 'flex';
}

function closeLightbox() {
  document.getElementById('modal-lightbox').style.display = 'none';
}

// ----------------------------------------------------------------------------- Finished Dataset (Phase 4)
async function loadDataset() {
  try {
    const res = await api('/api/dataset');
    state.dataset = res;
    document.getElementById('metric-total-exports').textContent = res.total_exports || 0;
    document.getElementById('metric-curated-videos').textContent = (res.per_video || []).length;

    const tbody = document.getElementById('dataset-table-body');
    tbody.innerHTML = '';
    if (!res.per_video || res.per_video.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-td">No datasets exported yet.</td></tr>';
      return;
    }

    res.per_video.forEach(v => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${v.name}</strong></td>
        <td><span class="badge">${v.count} frames</span></td>
        <td><code>data/finished/per_video/${v.slug}/</code></td>
        <td><code>data/finished/merged/</code></td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load dataset:', e);
  }
}

// ----------------------------------------------------------------------------- Player & Timeline Events
function setupEventListeners() {
  // Nav tabs
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.onclick = () => switchTab(tab.dataset.tab);
  });

  // Video playback timeupdate
  videoEl.ontimeupdate = () => {
    const cur = videoEl.currentTime;
    const dur = videoEl.duration || state.currentVideo?.duration || 0;
    document.getElementById('tc-current').textContent = formatTime(cur);
    document.getElementById('tc-total').textContent = formatTime(dur);

    if (dur > 0) {
      playheadEl.style.left = `${(cur / dur) * 100}%`;
    }
  };

  // Play/Pause
  const btnPlay = document.getElementById('btn-play-pause');
  btnPlay.onclick = () => {
    if (videoEl.paused) {
      videoEl.play();
      btnPlay.textContent = '⏸';
    } else {
      videoEl.pause();
      btnPlay.textContent = '▶';
    }
  };
  videoEl.onplay = () => btnPlay.textContent = '⏸';
  videoEl.onpause = () => btnPlay.textContent = '▶';

  // Frame Stepping
  document.getElementById('btn-step-back').onclick = () => stepFrame(-1);
  document.getElementById('btn-step-fwd').onclick = () => stepFrame(1);

  // Direct Timestamp Jump
  const jumpInput = document.getElementById('input-jump-time');
  const btnJumpGo = document.getElementById('btn-jump-go');

  const executeJump = () => {
    if (!jumpInput) return;
    const val = jumpInput.value;
    const t = parseTimestamp(val);
    if (t !== null) {
      jumpToTime(t);
      jumpInput.value = '';
    } else {
      jumpInput.style.borderColor = '#ef4444';
      setTimeout(() => { if (jumpInput) jumpInput.style.borderColor = ''; }, 1000);
    }
  };

  if (btnJumpGo) btnJumpGo.onclick = executeJump;
  if (jumpInput) {
    jumpInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        executeJump();
      }
    });
  }

  // Quick Jumps
  const btnJumpB60 = document.getElementById('btn-jump-back-60');
  const btnJumpB10 = document.getElementById('btn-jump-back-10');
  const btnJumpF10 = document.getElementById('btn-jump-fwd-10');
  const btnJumpF60 = document.getElementById('btn-jump-fwd-60');
  if (btnJumpB60) btnJumpB60.onclick = () => jumpToTime(videoEl.currentTime - 60);
  if (btnJumpB10) btnJumpB10.onclick = () => jumpToTime(videoEl.currentTime - 10);
  if (btnJumpF10) btnJumpF10.onclick = () => jumpToTime(videoEl.currentTime + 10);
  if (btnJumpF60) btnJumpF60.onclick = () => jumpToTime(videoEl.currentTime + 60);

  // Playback speed
  document.getElementById('playback-speed').onchange = (e) => {
    videoEl.playbackRate = parseFloat(e.target.value);
  };

  // Breakpoints
  document.getElementById('btn-mark-in').onclick = () => {
    state.inPoint = snapToNearestCut(videoEl.currentTime);
    updateInOutDisplay();
  };
  document.getElementById('btn-mark-out').onclick = () => {
    state.outPoint = snapToNearestCut(videoEl.currentTime);
    updateInOutDisplay();
  };
  document.getElementById('btn-add-chunk').onclick = addChunkFromInOut;

  // Snap Cuts toggle
  const snapToggle = document.getElementById('toggle-snap-cuts');
  snapToggle.onchange = (e) => state.snapCuts = e.target.checked;

  // Timeline seeking
  timelineTrackEl.onclick = (e) => {
    const rect = timelineTrackEl.getBoundingClientRect();
    const pos = (e.clientX - rect.left) / rect.width;
    const dur = videoEl.duration || state.currentVideo?.duration || 0;
    videoEl.currentTime = pos * dur;
  };

  // Timeline hover preview
  timelineTrackEl.onmousemove = (e) => {
    const rect = timelineTrackEl.getBoundingClientRect();
    const pos = (e.clientX - rect.left) / rect.width;
    const dur = videoEl.duration || state.currentVideo?.duration || 0;
    const hoverT = pos * dur;

    hoverPreviewEl.style.display = 'flex';
    hoverPreviewEl.style.left = `${e.clientX - rect.left}px`;
    hoverTimeEl.textContent = formatTime(hoverT);

    if (state.filmstrip.thumbnails && state.filmstrip.thumbnails.length > 0) {
      const idx = Math.min(
        state.filmstrip.thumbnails.length - 1,
        Math.max(0, Math.floor(pos * state.filmstrip.thumbnails.length))
      );
      hoverThumbEl.src = state.filmstrip.thumbnails[idx].url;
    }
  };
  timelineTrackEl.onmouseleave = () => {
    hoverPreviewEl.style.display = 'none';
  };

  // Search & Filters
  document.getElementById('video-search').oninput = debounce(loadVideos, 300);
  document.getElementById('status-filter').onchange = loadVideos;
  document.getElementById('btn-rescan').onclick = async () => {
    await api('/api/videos/scan', { method: 'POST' });
    loadVideos();
  };

  // Next video button
  document.getElementById('btn-next-video').onclick = selectNextVideo;

  // Auto-suggest chunks (Max 15)
  document.getElementById('btn-suggest-chunks').onclick = async () => {
    if (!state.currentVideo) return;
    try {
      const res = await api(`/api/videos/${state.currentVideo.id}/chunks/suggest`, {
        method: 'POST',
        body: JSON.stringify({ min_len_s: state.settings.suggest_min_len_s || 4.0, max_chunks: 15 }),
      });
      if (res.suggested && res.suggested.length > 0) {
        state.chunks = res.suggested;
        renderChunksList();
        renderTimelineChunks();
      } else {
        alert('No scene cuts detected to suggest chunks.');
      }
    } catch (e) {
      alert('Error suggesting chunks: ' + e.message);
    }
  };

  // Save chunks button
  document.getElementById('btn-save-chunks').onclick = () => saveChunks(true);

  // Extract & Score 750 candidates button
  const btnExtract750 = document.getElementById('btn-extract-750');
  if (btnExtract750) {
    btnExtract750.onclick = async () => {
      if (!state.currentVideo) {
        alert('Please select a video first.');
        return;
      }
      if (!state.chunks || state.chunks.length === 0) {
        alert('Please define at least one scene chunk before extracting.');
        return;
      }
      // Check and normalize budget
      let sumBudget = state.chunks.reduce((acc, c) => acc + (parseFloat(c.budget_pct) || 0), 0);
      if (Math.abs(sumBudget - 100) > 0.5) {
        if (sumBudget > 0) {
          state.chunks.forEach(c => {
            c.budget_pct = round((c.budget_pct / sumBudget) * 100, 1);
          });
        } else {
          normalizeChunkBudgets();
        }
        renderChunksList();
      }
      await saveChunks(false);
      try {
        const res = await api(`/api/videos/${state.currentVideo.id}/extract-750`, {
          method: 'POST',
          body: JSON.stringify({}),
        });
        showToast(
          'Extraction & Scoring Started',
          `Sampling 750 candidate frames across ${state.chunks.length} chunks by budget weights. Scoring 7 dimensions and selecting top 300...`,
          state.currentVideo.id
        );
      } catch (e) {
        alert('Error starting 750 candidate extraction: ' + e.message);
      }
    };
  }

  // Cancel job button (cancels active job for current video)
  const cancelJobBtn = document.getElementById('btn-cancel-job');
  if (cancelJobBtn) {
    cancelJobBtn.onclick = async () => {
      const activeJob = Array.from(state.activeJobs.values()).find(j => j.video_id === state.currentVideo?.id);
      if (activeJob) {
        await api(`/api/jobs/${activeJob.job_id}`, { method: 'DELETE' });
      }
    };
  }

  // Curation filters
  const curateChunkFilterEl = document.getElementById('curate-chunk-filter');
  if (curateChunkFilterEl) curateChunkFilterEl.onchange = renderCurationGrid;
  const curateRoleFilterEl = document.getElementById('curate-role-filter');
  if (curateRoleFilterEl) curateRoleFilterEl.onchange = renderCurationGrid;
  const curateStateFilterEl = document.getElementById('curate-state-filter');
  if (curateStateFilterEl) curateStateFilterEl.onchange = renderCurationGrid;

  // Toggle candidate pool (300 picks vs all 750)
  const btnTogglePool = document.getElementById('btn-toggle-candidate-pool');
  if (btnTogglePool) {
    btnTogglePool.onclick = () => {
      state.showRunnerUps = !state.showRunnerUps;
      btnTogglePool.textContent = state.showRunnerUps ? '👁️ View 300 Picks Only' : '👁️ View Candidate Pool (450)';
      renderCurationGrid();
    };
  }

  // Curation bulk actions
  document.getElementById('btn-curate-keep-all').onclick = async () => {
    const chunkFilter = document.getElementById('curate-chunk-filter')?.value || 'all';
    for (const c of state.candidates) {
      if (chunkFilter !== 'all' && String(c.chunk_id) !== chunkFilter) continue;
      if (c.state !== 'keep' && c.state !== 'star') {
        c.state = 'keep';
        api(`/api/picks/${c.id}`, { method: 'PATCH', body: JSON.stringify({ state: 'keep' }) });
      }
    }
    updateCurateChunkFilterOptions();
    renderCurationGrid();
  };
  document.getElementById('btn-curate-reject-all').onclick = async () => {
    const chunkFilter = document.getElementById('curate-chunk-filter')?.value || 'all';
    for (const c of state.candidates) {
      if (chunkFilter !== 'all' && String(c.chunk_id) !== chunkFilter) continue;
      if (c.role === 'pick') {
        await swapCandidate(c, false);
      }
    }
    updateCurateChunkFilterOptions();
    renderCurationGrid();
  };

  // OpenRouter Modal
  const openRouterModal = document.getElementById('modal-openrouter');
  const btnOpenRouterModal = document.getElementById('btn-openrouter-modal');
  const btnCloseOpenRouter = document.getElementById('btn-close-openrouter');
  const btnCancelOpenRouter = document.getElementById('btn-cancel-openrouter');
  const btnStartOpenRouter = document.getElementById('btn-start-openrouter');

  if (btnOpenRouterModal) {
    btnOpenRouterModal.onclick = () => {
      if (!state.currentVideo) {
        alert('Please select a video first.');
        return;
      }
      const picks = (state.candidates || []).filter(c => c.role === 'pick');
      if (picks.length === 0) {
        alert('No curated picks found for this video. Please run Extract & Score 750 first!');
        return;
      }
      const savedKey = localStorage.getItem('cp_openrouter_api_key') || '';
      const keyInput = document.getElementById('openrouter-api-key');
      if (keyInput && !keyInput.value && savedKey) {
        keyInput.value = savedKey;
      }
      if (openRouterModal) openRouterModal.style.display = 'flex';
    };
  }

  if (btnCloseOpenRouter) btnCloseOpenRouter.onclick = () => { if (openRouterModal) openRouterModal.style.display = 'none'; };
  if (btnCancelOpenRouter) btnCancelOpenRouter.onclick = () => { if (openRouterModal) openRouterModal.style.display = 'none'; };

  if (btnStartOpenRouter) {
    btnStartOpenRouter.onclick = async () => {
      if (!state.currentVideo) return;
      const apiKey = document.getElementById('openrouter-api-key').value.trim();
      const model = document.getElementById('openrouter-model-select').value;
      const mode = document.getElementById('openrouter-mode-select').value;
      const trigger = document.getElementById('openrouter-trigger-input').value.trim();
      const classNoun = document.getElementById('openrouter-class-input').value.trim();

      try {
        btnStartOpenRouter.disabled = true;
        btnStartOpenRouter.textContent = 'Queueing Batch...';

        const payload = {
          model: model || 'google/gemma-4-31b-it',
          fallback_layer2: 'deepseek/deepseek-v4.1-flash',
          fallback_layer3: 'x-ai/grok-4.7',
          reasoning_effort: 'high',
          max_tokens: 300,
          temperature: 0.80,
          mode: mode || 'concept',
          trigger: trigger || 'restrained_elegance',
          class_noun: classNoun || 'woman, ropes',
        };
        if (apiKey) {
          payload.api_key = apiKey;
          localStorage.setItem('cp_openrouter_api_key', apiKey);
        }

        const res = await api(`/api/videos/${state.currentVideo.id}/caption-openrouter`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        if (openRouterModal) openRouterModal.style.display = 'none';
        showToast(
          'OpenRouter 3-Layer Cascade Queued',
          `Queued ${res.candidate_count || 300} frames: L1 ${payload.model} (High Reasoning, max 300 tok) ➜ Refusal Fallback L2 ${payload.fallback_layer2} ➜ L3 ${payload.fallback_layer3}!`,
          state.currentVideo.id
        );

      } catch (err) {
        alert('Failed to launch OpenRouter batch: ' + err.message);
      } finally {
        btnStartOpenRouter.disabled = false;
        btnStartOpenRouter.textContent = '🚀 Start Batch Captioning (300 Frames)';
      }
    };
  };

  // Export video button
  document.getElementById('btn-export-this-video').onclick = async () => {
    if (!state.currentVideo) return;
    try {
      const res = await api('/api/export', {
        method: 'POST',
        body: JSON.stringify({ video_id: state.currentVideo.id }),
      });
      alert(`Exported ${res.exported} images successfully!`);
      loadVideos();
      switchTab('dataset');
    } catch (e) {
      alert('Export failed: ' + e.message);
    }
  };

  // Export all curated button
  document.getElementById('btn-export-all-curated').onclick = async () => {
    try {
      const res = await api('/api/export', { method: 'POST', body: JSON.stringify({}) });
      alert(`Exported total ${res.exported} images across ${res.videos_processed} videos!`);
      loadDataset();
      loadVideos();
    } catch (e) {
      alert('Bulk export failed: ' + e.message);
    }
  };

  document.getElementById('btn-refresh-dataset').onclick = loadDataset;

  // Close lightbox
  document.getElementById('btn-close-lightbox').onclick = closeLightbox;

  // Status select change
  document.getElementById('video-status-select').onchange = async (e) => {
    if (!state.currentVideo) return;
    await api(`/api/videos/${state.currentVideo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: e.target.value }),
    });
    state.currentVideo.status = e.target.value;
    loadVideos();
  };

  // Settings modal
  const modal = document.getElementById('modal-settings');
  document.getElementById('btn-settings-modal').onclick = () => modal.style.display = 'flex';
  document.getElementById('btn-close-settings').onclick = () => modal.style.display = 'none';
  document.getElementById('btn-save-settings').onclick = async () => {
    const payload = {
      default_k: parseInt(document.getElementById('setting-k').value),
      default_rule: document.getElementById('setting-rule').value,
      sample_fps: parseFloat(document.getElementById('setting-fps').value),
      survivor_frac: parseFloat(document.getElementById('setting-survivor').value),
      trigger_word: document.getElementById('setting-trigger').value,
      max_edge: parseInt(document.getElementById('setting-max-edge').value),
    };
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    state.settings = { ...state.settings, ...payload };
    modal.style.display = 'none';
  };

  // Keyboard Shortcuts
  window.addEventListener('keydown', handleKeyboardShortcuts);
}

function handleKeyboardShortcuts(e) {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;

  switch (e.key) {
    case ' ':
      e.preventDefault();
      document.getElementById('btn-play-pause').click();
      break;
    case 'k':
    case 'K':
      if (document.getElementById('modal-lightbox').style.display === 'flex') {
        document.getElementById('lb-btn-keep').click();
      } else {
        videoEl.pause();
      }
      break;
    case 'x':
    case 'X':
      if (document.getElementById('modal-lightbox').style.display === 'flex') {
        document.getElementById('lb-btn-reject').click();
      }
      break;
    case 's':
    case 'S':
      if (document.getElementById('modal-lightbox').style.display === 'flex') {
        document.getElementById('lb-btn-star').click();
      }
      break;
    case 'Escape':
      closeLightbox();
      break;
    case 'g':
    case 'G':
      e.preventDefault();
      const jInput = document.getElementById('input-jump-time');
      if (jInput) {
        jInput.focus();
        jInput.select();
      }
      break;
    case 'PageUp':
      e.preventDefault();
      jumpToTime(videoEl.currentTime + 60);
      break;
    case 'PageDown':
      e.preventDefault();
      jumpToTime(videoEl.currentTime - 60);
      break;
    case 'j':
    case 'J':
      jumpToTime(videoEl.currentTime - 2);
      break;
    case 'l':
    case 'L':
      jumpToTime(videoEl.currentTime + 2);
      break;
    case 'ArrowLeft':
      e.preventDefault();
      const stepLeft = e.altKey ? 300 : (e.ctrlKey ? 60 : (e.shiftKey ? 10 : 1));
      jumpToTime(videoEl.currentTime - stepLeft);
      break;
    case 'ArrowRight':
      e.preventDefault();
      const stepRight = e.altKey ? 300 : (e.ctrlKey ? 60 : (e.shiftKey ? 10 : 1));
      jumpToTime(videoEl.currentTime + stepRight);
      break;
    case ',':
      stepFrame(-1);
      break;
    case '.':
      stepFrame(1);
      break;
    case '[':
    case 'i':
    case 'I':
      document.getElementById('btn-mark-in').click();
      break;
    case ']':
    case 'o':
    case 'O':
      document.getElementById('btn-mark-out').click();
      break;
    case 'Enter':
      if (state.currentTab === 'studio') {
        document.getElementById('btn-add-chunk').click();
      }
      break;
    case 'c':
    case 'C':
      const toggle = document.getElementById('toggle-snap-cuts');
      toggle.checked = !toggle.checked;
      state.snapCuts = toggle.checked;
      break;
    case 'n':
    case 'N':
      selectNextVideo();
      break;
  }
}

function stepFrame(dir) {
  const fps = state.currentVideo?.fps || 25.0;
  videoEl.pause();
  videoEl.currentTime = Math.max(0, Math.min(videoEl.duration, videoEl.currentTime + (dir / fps)));
}

function round(val, digits = 2) {
  return Number(Math.round(val + 'e' + digits) + 'e-' + digits);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
