/**
 * Deterministic API fixtures for the UX gate (Playwright + Lighthouse CI). Shapes follow
 * .agents/skills/api-contract/SKILL.md and src/web/app/core/models. Everything is generated from constants,
 * so every run renders exactly the same DOM.
 */

export const VIDEO_ID = 'vid-ux-001';
const VIDEO_2 = 'vid-ux-002';

const CHUNK_DEFS = [
  { id: 101, position: 0, start_s: 12, end_s: 58, tags: ['intro', 'studio'], rule: 'person' },
  { id: 102, position: 1, start_s: 64, end_s: 140, tags: ['workshop'], rule: 'either' },
  { id: 103, position: 2, start_s: 151, end_s: 233, tags: ['outro'], rule: 'person' },
];
const PICKS_PER_CHUNK = 6;
const RUNNERS_PER_CHUNK = 2;

export function makeVideos() {
  return [
    {
      id: VIDEO_ID,
      name: 'Workshop session A.mp4',
      path: '/videos/Workshop session A.mp4',
      slug: 'workshop-session-a',
      width: 1920,
      height: 1080,
      duration: 245.5,
      fps: 25,
      frame_count: 6137,
      broken: false,
      status: 'curating_300',
      profile: 'concept',
      watermark_rect: { x: 0.78, y: 0.9, width: 0.2, height: 0.08 },
      letterbox: null,
      chunk_count: CHUNK_DEFS.length,
      export_count: 12,
      kept_count: 18,
      captioned_count: 12,
      target_person_id: null,
      target_person_name: null,
      positive_persons: [],
      negative_persons: [],
      autopilot: null,
      autopilot_stage: null,
    },
    {
      id: VIDEO_2,
      name: 'Interview B.mp4',
      path: '/videos/Interview B.mp4',
      slug: 'interview-b',
      width: 1920,
      height: 1080,
      duration: 512,
      fps: 30,
      frame_count: 15360,
      broken: false,
      status: 'new',
      profile: 'concept',
      watermark_rect: null,
      letterbox: null,
      chunk_count: 0,
      export_count: 0,
      kept_count: 0,
      captioned_count: 0,
      autopilot: null,
    },
  ];
}

export function makeChunks() {
  const budget = [34, 33, 33];
  return CHUNK_DEFS.map((c, i) => ({
    ...c,
    k: 8,
    budget_pct: budget[i],
    caption_model: 'google/gemma-4-31b-it',
    priority: 'normal',
    person_presence: 0.8,
    face_presence: 0.6,
    captioned_count: 4,
    picks_count: PICKS_PER_CHUNK,
    exports_count: 4,
    total_frames: PICKS_PER_CHUNK + RUNNERS_PER_CHUNK,
    is_fully_captioned: false,
    caption_progress_pct: 66,
  }));
}

const LONG_CAPTIONS = [
  'A craftsman in a dark apron stands at a wooden workbench in a brightly lit studio, holding a chisel above a half-carved maple board while wood shavings scatter across the surface; shelves with clamps, hand planes and glass jars of screws line the back wall, warm daylight enters from a tall window on the left and soft shadows fall across his focused face.',
  'Two people lean over a large drafting table covered with blueprints, one pointing at a measurement with a pencil while the other holds a steel ruler; a desk lamp casts a pool of warm light, coffee mugs and a laptop sit at the edge, and a pegboard full of tools fills the background of the tidy workshop.',
];

function makeFrame(chunk, j, isPick) {
  const id = chunk.id * 100 + j;
  const span = chunk.end_s - chunk.start_s;
  const t = Number((chunk.start_s + ((j + 0.5) * span) / (PICKS_PER_CHUNK + RUNNERS_PER_CHUNK)).toFixed(2));
  const states = ['keep', 'star', 'keep', 'reject', 'keep', 'keep'];
  const captioned = isPick && j % 3 !== 2;
  return {
    id,
    video_id: VIDEO_ID,
    chunk_id: chunk.id,
    t,
    time_s: t,
    role: isPick ? 'pick' : 'runner_up',
    is_pick: isPick,
    rank: j + 1,
    state: isPick ? states[j % states.length] : null,
    score: Number((0.92 - j * 0.03).toFixed(3)),
    metrics: { sharpness: 0.81 - j * 0.02, quality: 0.77 - j * 0.015, final_score: 0.9 - j * 0.03 },
    zs_scores: { multiple_people: j % 2 === 0 ? 0.71 : 0.21, one_person: 0.6, tools_equipment: 0.55, face_visible: 0.66 },
    categories: { multiple_people: j % 2 === 0, one_person: j % 2 === 1, tools_equipment: j % 3 === 0, face_visible: j % 4 !== 3 },
    preview_url: `/api/frames/${id}/preview`,
    full_url: `/api/frames/${id}/full`,
    is_captioned: captioned,
    caption_text: captioned ? LONG_CAPTIONS[j % 2] : undefined,
    chunk_position: chunk.position,
    chunk_start: chunk.start_s,
    chunk_end: chunk.end_s,
    chunk_tags: chunk.tags,
    face_count: 1,
    tags: [],
    exported: isPick && j < 4,
  };
}

export function makeCuration() {
  const picks = [];
  const runners = [];
  for (const chunk of CHUNK_DEFS) {
    for (let j = 0; j < PICKS_PER_CHUNK; j++) picks.push(makeFrame(chunk, j, true));
    for (let j = 0; j < RUNNERS_PER_CHUNK; j++) runners.push(makeFrame(chunk, PICKS_PER_CHUNK + j, false));
  }
  const stat = (pct, target_pct) => ({ pct, target_pct, count: Math.round((pct / 100) * picks.length) });
  const distribution = {
    multiple_people: stat(50, 50),
    one_person: stat(28, 27.5),
    tools_equipment: stat(22, 22.5),
    face_visible: stat(64, 50),
    selected_count: picks.length,
    target_count: 300,
  };
  return { picks, runners, candidates: [...picks, ...runners], chunks: makeChunks(), distribution, stats: distribution };
}

export function makeDataset() {
  return {
    total_exports: 12,
    per_video: [{ id: VIDEO_ID, name: 'Workshop session A.mp4', slug: 'workshop-session-a', count: 12 }],
    finished_dir: '/data/finished',
  };
}

export function makeSettings() {
  return {
    default_k: 8,
    default_rule: 'person',
    sample_fps: 2,
    survivor_frac: 0.3,
    trigger_word: 'ohwx',
    max_edge: 1536,
    snap_to_cuts: true,
    snap_window_s: 1.5,
    suggest_min_len_s: 4,
    openrouter_api_key_set: true,
    use_learned_weights: false,
    reselect_diversity: 0.5,
    reselect_elasticity: 0.3,
    reselect_axis_caps: {},
    reselect_cross_dataset_dedup: false,
    tag_suggest_min_score: 0.6,
    auto_suggest_tags: false,
    dhash_dedup_max_distance: 6,
    window_peak_sampling: true,
    reuse_analysis_cache: true,
    quality_v2: true,
    cut_detector: 'content',
    selection_embedder: 'clip',
    iqa_model: 'off',
    rag_auto_reindex: false,
    openrouter_mode: 'concept',
    openrouter_model: 'google/gemma-4-31b-it',
  };
}

export const OPENROUTER_MODELS = [
  { id: 'google/gemma-4-31b-it', name: 'Google Gemma 4 31B (Default)', recommended: true },
  { id: 'deepseek/deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash (Layer 2 Fallback)', recommended: false },
  { id: 'x-ai/grok-4.7', name: 'Grok 4.7 (Layer 3 Final Fallback, Uncensored)', recommended: false },
];

export function makeFilmstrip() {
  const thumbnails = Array.from({ length: 12 }, (_, i) => ({ time_s: i * 20, url: `/api/frames/${10100 + (i % 6)}/preview` }));
  return { video_id: VIDEO_ID, duration: 245.5, thumbnails, cuts: [12, 58, 64, 140, 151, 233] };
}

/** Caption tiers per frame (MOSS 3-tier editor). Derived deterministically from the long caption. */
export function makeCaptionTiers(frameId, longText) {
  const long = longText ?? '';
  const sentences = long.split(/(?<=[.;])\s+/);
  const words = long.split(/\s+/).filter(Boolean);
  return {
    frame_id: frameId,
    long,
    medium: words.slice(0, 45).join(' '),
    short: (sentences[0] ?? '').split(/\s+/).slice(0, 14).join(' '),
    source: long ? 'vlm' : 'empty',
  };
}

// ================================================================================================ /api/v1 (Studio)
export const DATASET_ID = 'ds-ux-001';
const STUDIO_ITEM_COUNT = 24;
const CAPTION_STATUSES = ['vlm_accepted', 'vlm_accepted', 'uncaptioned', 'needs_manual_caption', 'vlm_accepted', 'manual'];
const LAYERS = { vlm_accepted: 'primary', manual: 'manual', needs_manual_caption: null, uncaptioned: null };
const CURATION = ['keep', 'keep', 'star', 'keep', 'candidate', 'reject'];
const TAG_POOL = [
  { name: 'standing', slug: 'standing', category: 'pose' },
  { name: 'soft window light', slug: 'soft-window-light', category: 'lighting' },
  { name: 'workbench', slug: 'workbench', category: 'equipment' },
  { name: 'medium shot', slug: 'medium-shot', category: 'shot_scale' },
  { name: 'workshop', slug: 'workshop', category: 'environment' },
];

export function makeStudioDatasets() {
  return [
    {
      id: DATASET_ID,
      name: 'Workshop Character Set',
      slug: 'workshop-character-set',
      source_type: 'video',
      source_path: '/input/workshop-session-a.mp4',
      status: 'captioning',
      trigger_word: 'ohwx_subject',
      trigger_position: 'prefix',
      active_skill_id: 'skill-character',
      target_count: 300,
      dedup_hamming_threshold: 8,
      video_metadata: { duration_s: 245.5, fps: 25, width: 1920, height: 1080 },
      stats_cache: {},
      total_items: STUDIO_ITEM_COUNT,
      kept_items: 16,
      rejected_items: 4,
      duplicate_items: 2,
      captioned_items: 12,
      needs_manual_items: 4,
      created_at: 1793000000,
      updated_at: 1793003600,
    },
  ];
}

export function makeStudioItems() {
  return Array.from({ length: STUDIO_ITEM_COUNT }, (_, i) => {
    const id = 5001 + i;
    const status = CAPTION_STATUSES[i % CAPTION_STATUSES.length];
    const curation = CURATION[i % CURATION.length];
    const captioned = status === 'vlm_accepted' || status === 'manual';
    return {
      id,
      dataset_id: DATASET_ID,
      cluster_id: Math.floor(i / 3),
      is_cluster_representative: i % 3 === 0,
      cluster_size: 3,
      source_filename: `workshop-session-a_scene${String(i + 1).padStart(3, '0')}.png`,
      source_path: `/input/workshop-session-a.mp4`,
      extracted_path: `/appdata/frames/${id}.png`,
      thumbnail_path: `/appdata/thumbs/${id}.webp`,
      thumbnail_url: `/api/v1/media/${id}/thumbnail`,
      full_url: `/api/v1/media/${id}/full`,
      timestamp_s: Number((8 + i * 9.7).toFixed(2)),
      scene_index: i,
      frame_extract_type: 'scene_cut',
      width: 1920,
      height: 1080,
      aspect_ratio: 1.7778,
      phash: (0xf0e19a3c + i).toString(16),
      dhash: (0x0a1b2c3d + i).toString(16),
      sharpness_laplacian: 412 - i * 6,
      sharpness_tenengrad: 18.2 - i * 0.2,
      sharpness_score: Number((0.86 - i * 0.012).toFixed(3)),
      exposure_mean_luma: 0.48,
      exposure_clipping_frac: 0.01,
      exposure_score: 0.91,
      entropy_shannon: 7.1,
      entropy_score: 0.88,
      contrast_rms: 0.21,
      quality_score: Number((0.89 - i * 0.011).toFixed(3)),
      curation_state: curation,
      reject_reason: curation === 'reject' ? 'motion blur' : null,
      crop_geometry: null,
      is_outlier: i === 7,
      active_caption: captioned ? `ohwx_subject, ${LONG_CAPTIONS[i % 2]}` : null,
      caption_medium: captioned ? `ohwx_subject, ${LONG_CAPTIONS[i % 2].split(/\s+/).slice(0, 45).join(' ')}` : null,
      caption_short: captioned ? `ohwx_subject, ${LONG_CAPTIONS[i % 2].split(/\s+/).slice(0, 14).join(' ')}` : null,
      caption_status: status,
      active_caption_model: captioned ? (status === 'manual' ? 'manual' : 'google/gemma-4-31b-it') : null,
      active_caption_layer: LAYERS[status] ?? null,
      tags: TAG_POOL.slice(0, 2 + (i % 3)).map((t) => ({ ...t, source: 'rule', confidence: 0.82 })),
      created_at: 1793000000 + i,
      updated_at: 1793000000 + i,
    };
  });
}

export function makeStudioSettings() {
  return {
    primary_model: 'google/gemma-4-31b-it',
    fallback_model_1: 'deepseek/deepseek-v4.1-flash',
    fallback_model_2: 'x-ai/grok-4.7',
    max_tokens: 600,
    temperature: 0.4,
    top_p: 0.95,
    max_image_dim: 1024,
    trigger_word: 'ohwx_subject',
    trigger_position: 'prefix',
    custom_rejection_regexes: [],
    quality_weights: { sharpness: 0.4, exposure: 0.25, entropy: 0.2, contrast: 0.15 },
    rag_dense_backend: 'tfidf_svd_128',
    rag_embedding_model: 'openai/text-embedding-3-small',
    openrouter_api_key_set: true,
    openrouter_api_key_masked: 'sk-or-v1-****7f3a',
  };
}

export function makeStudioSkills() {
  return [
    {
      id: 'skill-character',
      slug: 'character-lora',
      name: 'Character LoRA',
      version: 3,
      training_type: 'character',
      system_prompt_template: 'You caption images for character LoRA training.',
      user_prompt_template: 'Describe the image. Trigger: {trigger_word}.',
      glossary_template: '',
      min_words: 80,
      max_words: 180,
      hard_min_words: 40,
      hard_max_words: 240,
      discouraged_regexes: [],
      require_class_noun: true,
      is_builtin: true,
      created_at: 1793000000,
      updated_at: 1793000000,
    },
  ];
}

export function makeStratification() {
  return {
    total_items: STUDIO_ITEM_COUNT,
    aspect_ratio_buckets: { '16:9': 20, '4:3': 3, '1:1': 1 },
    brightness_buckets: { low_key: 5, mid_key: 15, high_key: 4 },
    color_temp_buckets: { warm: 12, neutral: 9, cool: 3 },
    temporal_deciles: [3, 2, 3, 2, 2, 3, 2, 3, 2, 2],
    quality_quartiles: { q1: 0.71, q2: 0.78, q3: 0.84, q4: 0.89 },
    quality_mean: 0.78,
    quality_std: 0.06,
    tag_category_coverage: {
      pose: { total_assignments: 24, unique_tags: 3, top_tags: { standing: 14, seated: 6, leaning: 4 } },
      lighting: { total_assignments: 24, unique_tags: 2, top_tags: { 'soft window light': 18, 'hard key': 6 } },
    },
    outlier_ids: [5008],
    recommendations: ['Add 4 more low-key frames to balance the brightness distribution.'],
  };
}

export function makeRejections() {
  return {
    needs_manual_count: 4,
    logs: [
      {
        id: 1,
        media_id: 5004,
        dataset_id: DATASET_ID,
        job_id: 7,
        cascade_layer: 'primary',
        model_attempted: 'google/gemma-4-31b-it',
        rejection_type: 'refusal_regex',
        rejection_reasons: ['matched refusal pattern "I can\'t help with"'],
        raw_response_snippet: "I can't help with describing this image.",
        finish_reason: 'stop',
        prompt_tokens: 812,
        completion_tokens: 14,
        cost_usd: 0.0004,
        created_at: 1793001000,
      },
    ],
  };
}
