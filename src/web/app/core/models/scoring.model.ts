/** Phase F: reselect knobs and report, learned quality weights (see api-contract "Phase F"). */

export type ScoreComponent = 'sharpness' | 'quality' | 'persons' | 'objects' | 'face';

/** Body of `POST /api/videos/{id}/reselect`; every field is optional (server defaults from the settings). */
export interface ReselectRequest {
  /** 1-5000; default = current pick count. */
  target_count?: number;
  /** Absolute floor/cap for every chunk (override the elasticity-derived ones). */
  per_chunk_floor?: number;
  per_chunk_cap?: number;
  /** 0 = pure quality ranking, 1 = pure coverage. Default: setting `reselect_diversity` (0.5). */
  diversity?: number;
  /** Budget strictness when floor/cap are not given. Default: setting `reselect_elasticity` (0.2). */
  elasticity?: number;
  /** Max share of picks per category axis (0-1); `{}` turns stored caps off. Default: `reselect_axis_caps`. */
  axis_caps?: Partial<Record<ReselectAxis, number>>;
  /** Skip near-duplicates of frames already exported from other videos. Default: `reselect_cross_dataset_dedup`. */
  cross_dataset_dedup?: boolean;
}

export type ReselectAxis = 'multi_person' | 'solo' | 'equipment' | 'face';
export const RESELECT_AXES: readonly ReselectAxis[] = ['multi_person', 'solo', 'equipment', 'face'];

export interface ReselectChunkReport {
  chunk_id: number;
  quota: number;
  floor: number;
  cap: number;
  pool: number;
  picks: number;
}

/** `reselect` field of the reselect response. */
export interface ReselectReport {
  target_count: number;
  picks_count: number;
  shortfall: number;
  promoted: number[];
  demoted: number[];
  /** Starred/exported frames that always stay picks. */
  locked: number;
  dup_skipped: number;
  excluded_rejected: number;
  diversity: number;
  elasticity: number;
  learned_weights_active: boolean;
  chunks: ReselectChunkReport[];
  embedder?: string;
  embedder_fallback?: string | null;
  axis_caps?: Partial<Record<ReselectAxis, { max_share: number; cap: number; picks: number; pool: number; overflow: number }>>;
  cross_dataset?: { enabled: boolean; reference_frames: number; blocked: number; blocked_ids?: number[] };
}

/** `GET /api/scoring/learned-weights` and `POST .../fit`. */
export interface LearnedWeightsStatus {
  weights: Partial<Record<ScoreComponent, number>> | null;
  default_weights: Partial<Record<ScoreComponent, number>>;
  coefficients?: Partial<Record<ScoreComponent, number>> | null;
  intercept?: number | null;
  /** Labelled curation events now. */
  n_events: number;
  n_events_at_fit?: number | null;
  n_positive?: number;
  n_negative?: number;
  /** Training accuracy (not held out), 0..1. */
  accuracy: number | null;
  auc?: number | null;
  fitted_at?: number | string | null;
  fitted: boolean;
  min_events: number;
  can_fit: boolean;
  use_learned_weights: boolean;
  blend?: number;
  /** Fitted, enabled and enough events: new scores and reselect use the blend. */
  active: boolean;
  features?: ScoreComponent[];
}
