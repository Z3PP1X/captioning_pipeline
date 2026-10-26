/**
 * Canonical TypeScript interfaces for the LoRA Dataset & Captioning Studio (/api/v1/*).
 * Designed for strict Angular Signals + TypeScript `noPropertyAccessFromIndexSignature: true`.
 */

export type StudioStep =
  | 'ingest'
  | 'triage'
  | 'tagging'
  | 'captioning'
  | 'export_rag';

export type SourceType = 'image_folder' | 'video' | 'hybrid';
export type TriggerPosition = 'prefix' | 'suffix' | 'natural_integration';
export type ExtractionMode = 'scene' | 'iframe' | 'interval';

export type CurationState =
  | 'keep'
  | 'star'
  | 'reject'
  | 'candidate'
  | 'duplicate_hidden'
  | 'auto_rejected';

export type CaptionStatus =
  | 'uncaptioned'
  | 'queued'
  | 'vlm_accepted'
  | 'needs_manual_caption'
  | 'manual'
  | 'approved';

export type TagSource = 'manual' | 'rule' | 'ml_plugin';
export type TagCategory =
  | 'pose'
  | 'lighting'
  | 'outfit'
  | 'expression'
  | 'style_element'
  | 'shot_scale'
  | 'environment'
  | 'equipment';

export interface StudioTag {
  name: string;
  slug: string;
  source: TagSource;
  plugin_id?: string;
  category: TagCategory;
  confidence: number;
  bbox?: { x: number; y: number; w: number; h: number } | null;
}

export interface HealthStatus {
  status: string;
  sqlite_wal: boolean;
  ffmpeg: boolean;
  fts5: boolean;
  mounts: {
    input: boolean;
    exports: boolean;
    appdata: boolean;
  };
}

export interface QualityWeightsConfig {
  sharpness: number;
  exposure: number;
  entropy: number;
  contrast: number;
}

export interface SystemSettingsMap {
  primary_model?: string;
  fallback_model_1?: string;
  fallback_model_2?: string;
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  max_image_dim?: number;
  trigger_word?: string;
  trigger_position?: TriggerPosition;
  custom_rejection_regexes?: string[];
  quality_weights?: QualityWeightsConfig;
  rag_dense_backend?: 'tfidf_svd_128' | 'openrouter_embedding';
  rag_embedding_model?: string;
  openrouter_api_key_set?: boolean;
  openrouter_api_key_masked?: string;
}

export interface OpenRouterTestResponse {
  valid: boolean;
  label?: string;
  usage_usd?: number;
  limit_usd?: number | null;
  error?: string;
}

export interface FsBrowseResponse {
  current_path: string;
  directories: string[];
  videos: string[];
  image_counts: number;
}

export interface ExtractionConfigDto {
  mode: ExtractionMode;
  scene_threshold: number;
  interval_s: number;
  window_peak: number;
  target_candidates: number;
  dedup_hamming_threshold: number;
}

export interface StudioDataset {
  id: string;
  name: string;
  slug: string;
  source_type: SourceType;
  source_path: string;
  status:
    | 'created'
    | 'ingesting'
    | 'curating'
    | 'captioning'
    | 'ready_to_export'
    | 'exported'
    | 'error';
  trigger_word: string;
  trigger_position: TriggerPosition;
  active_skill_id: string | null;
  target_count: number;
  dedup_hamming_threshold: number;
  video_metadata: Record<string, unknown>;
  stats_cache: Record<string, unknown>;
  total_items: number;
  kept_items: number;
  rejected_items: number;
  duplicate_items: number;
  captioned_items: number;
  needs_manual_items: number;
  created_at: number;
  updated_at: number;
}

export interface StudioMediaItem {
  id: number;
  dataset_id: string;
  cluster_id: number | null;
  is_cluster_representative: number | boolean;
  cluster_size: number;
  source_filename: string;
  source_path: string;
  extracted_path: string | null;
  thumbnail_path: string | null;
  thumbnail_url: string;
  full_url: string;
  timestamp_s: number | null;
  scene_index: number;
  frame_extract_type: 'image' | 'scene_cut' | 'iframe' | 'interval_peak';
  width: number;
  height: number;
  aspect_ratio: number;
  phash: string;
  dhash: string;
  sharpness_laplacian: number;
  sharpness_tenengrad: number;
  sharpness_score: number;
  exposure_mean_luma: number;
  exposure_clipping_frac: number;
  exposure_score: number;
  entropy_shannon: number;
  entropy_score: number;
  contrast_rms: number;
  quality_score: number;
  curation_state: CurationState;
  reject_reason: string | null;
  crop_geometry: { x: number; y: number; width: number; height: number } | null;
  is_outlier: number | boolean;
  active_caption: string | null;
  caption_status: CaptionStatus;
  active_caption_model: string | null;
  active_caption_layer: 'primary' | 'fallback_1' | 'fallback_2' | 'manual' | 'cached' | null;
  tags: StudioTag[];
  created_at: number;
  updated_at: number;
}

export interface TagCategoryCoverageEntry {
  total_assignments: number;
  unique_tags: number;
  top_tags: Record<string, number>;
}

export interface StratificationReport {
  total_items: number;
  aspect_ratio_buckets: Record<string, number>;
  brightness_buckets: Record<string, number>;
  color_temp_buckets: Record<string, number>;
  temporal_deciles: number[];
  quality_quartiles: Record<string, number>;
  quality_mean?: number;
  quality_std?: number;
  tag_category_coverage: Record<string, TagCategoryCoverageEntry>;
  outlier_ids: number[];
  recommendations: string[];
}

export interface StratifyResult {
  kept_count: number;
  promoted_ids: number[];
  demoted_ids: number[];
  outlier_ids: number[];
  distribution: Record<string, unknown>;
}

export interface StudioSkill {
  id: string;
  slug: string;
  name: string;
  version: number;
  training_type: 'character' | 'concept' | 'style' | 'general';
  system_prompt_template: string;
  user_prompt_template: string;
  glossary_template: string;
  min_words: number;
  max_words: number;
  hard_min_words: number;
  hard_max_words: number;
  discouraged_regexes: string[];
  require_class_noun: number | boolean;
  is_builtin: number | boolean;
  created_at: number;
  updated_at: number;
}

export interface RejectionLogEntry {
  id: number;
  media_id: number;
  dataset_id: string;
  job_id: number | null;
  cascade_layer: 'primary' | 'fallback_1' | 'fallback_2';
  model_attempted: string;
  rejection_type:
    | 'refusal_regex'
    | 'provider_safety_filter'
    | 'too_short'
    | 'truncated_length'
    | 'meta_preamble'
    | 'minor_coded_rule'
    | 'http_error';
  rejection_reasons: string[];
  raw_response_snippet: string;
  finish_reason: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  created_at: number;
}

export interface RejectionsResponse {
  needs_manual_count: number;
  logs: RejectionLogEntry[];
}

export interface DatasetCardSummary {
  dataset_id: string;
  dataset_name: string;
  slug: string;
  trigger_word: string;
  trigger_position: TriggerPosition;
  exported_items: number;
  image_format: 'png' | 'jpg';
  max_edge: number;
  snap_multiple_16: boolean;
  resolution_buckets?: Record<string, number>;
  aspect_ratio_buckets?: Record<string, number>;
  cascade_layer_counts?: Record<string, number>;
  word_count_stats?: {
    min: number;
    max: number;
    mean: number;
  };
  tag_distribution?: Record<string, number>;
}

export interface DatasetExportResponse {
  exported_count: number;
  export_dir: string;
  metadata_jsonl_path: string;
  dataset_card_path?: string;
  dataset_card: DatasetCardSummary;
  zip_path?: string | null;
  zip_download_url?: string | null;
}

export interface RagIndexResponse {
  indexed_documents: number;
  fts5_ready: boolean;
  dense_backend: 'tfidf_svd_128' | 'openrouter_embedding';
  duration_s: number;
}

export interface RagRetrievedExemplar {
  doc_id: number;
  media_id: number;
  dataset_id: string;
  stem: string;
  trigger_word: string;
  score: number;
  bm25_score: number;
  cosine_score: number;
  tag_overlap_score?: number;
  matched_tags: string[];
  full_caption: string;
  equipment_text?: string;
  surroundings_text?: string;
  position_text?: string;
  lighting_text?: string;
  thumbnail_url?: string;
}

export interface RagArchetypeCard {
  name?: string;
  prompt: string;
  token_count: number;
  warnings?: string[];
}

export interface RagGenerateResponse {
  model: string;
  trigger_word: string;
  style_trigger?: string | null;
  character?: string | null;
  grounded: boolean;
  notices?: string[];
  archetypes: {
    a: RagArchetypeCard;
    b: RagArchetypeCard;
    c: RagArchetypeCard;
  };
  rag_context: RagRetrievedExemplar[];
}

export interface StudioTelemetryEvent {
  type:
    | 'job_started'
    | 'job_progress'
    | 'job_finished'
    | 'job_error'
    | 'job_cancelled'
    | 'caption_accepted'
    | 'caption_rejected'
    | 'connected';
  timestamp: number;
  data: {
    job_id?: number;
    dataset_id?: string;
    media_id?: number;
    progress?: number;
    step?: string;
    layer?: string;
    model?: string;
    rejection_type?: string;
    reasons?: string[];
    cost_usd?: number;
  };
}

export interface DiffToken {
  type: 'same' | 'added' | 'removed';
  text: string;
}
