export interface PipelineSettings {
  default_k: number;
  default_rule: 'person' | 'equipment' | 'both' | 'either' | 'none' | string;
  sample_fps: number;
  survivor_frac: number;
  trigger_word: string;
  max_edge: number;
  normalize_to_buckets?: boolean;
  export_bucket_preset?: '4_buckets' | '3_buckets';
  snap_to_cuts?: boolean;
  snap_window_s?: number;
  suggest_min_len_s?: number;
  /** Write-only: sent to PUT /api/settings to store the key on the server; GET never returns it. */
  openrouter_api_key?: string;
  /** Read-only flag from GET /api/settings: an OpenRouter key is stored on the server. */
  openrouter_api_key_set?: boolean;
  /** Phase F: blend the fitted keep/reject weights into new scores and reselect (default false). */
  use_learned_weights?: boolean;
  /** Phase F reselect defaults (0..1). */
  reselect_diversity?: number;
  reselect_elasticity?: number;
  reselect_axis_caps?: Partial<Record<'multi_person' | 'solo' | 'equipment' | 'face', number>>;
  reselect_cross_dataset_dedup?: boolean;
  /** Image tagging: minimum score for a suggestion; pre-tag new extractions. */
  tag_suggest_min_score?: number;
  auto_suggest_tags?: boolean;
  /** Phase F extraction / scoring. */
  dhash_dedup_max_distance?: number;
  window_peak_sampling?: boolean;
  reuse_analysis_cache?: boolean;
  quality_v2?: boolean;
  cut_detector?: 'content' | 'legacy';
  selection_embedder?: 'clip' | 'dinov2';
  iqa_model?: 'off' | 'clip_iqa' | 'topiq_nr';
  /** Prompt Director: incremental reindex after each caption job. */
  rag_auto_reindex?: boolean;
}
