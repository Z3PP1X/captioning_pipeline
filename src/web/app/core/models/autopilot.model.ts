import { ChunkItem } from './chunk.model';

export type ChunkPriority = 'skip' | 'low' | 'normal' | 'high';

export type AutopilotStage =
  | 'idle'
  | 'planning'
  | 'awaiting_review'
  | 'extracting'
  | 'refining'
  | 'ready'
  | 'captioning'
  | 'failed';

export interface AutopilotConfig {
  exclude_ranges?: Array<[number, number] | string>;
  target_person_id?: number | null;
  target_person_name?: string | null;
  review_chunks?: boolean;
  min_scene_s?: number;
  max_scene_s?: number;
  k_per_chunk?: number;
  caption_model?: string;
  auto_caption?: boolean;
  auto_export?: boolean;
}

export interface AutopilotMetrics {
  total_candidates?: number;
  kept_candidates?: number;
  target_person_ratio?: number;
  duplicate_pairs_removed?: number;
  off_target_purged?: number;
  refilled_candidates?: number;
  mean_clip_similarity?: number;
  [key: string]: any;
}

export interface AutopilotStateData {
  stage: AutopilotStage;
  config?: AutopilotConfig;
  message?: string;
  step?: string;
  error?: string;
  metrics?: AutopilotMetrics;
}

export interface AutopilotStatusResponse {
  video_id: string;
  target_person_id: number | null;
  target_person_name: string | null;
  autopilot: AutopilotStateData | null;
  chunks: ChunkItem[];
  metrics: AutopilotMetrics;
}

export interface AutopilotStartPayload {
  exclude_ranges?: Array<string | [number, number]>;
  target_person_id?: number | null;
  target_person_name?: string | null;
  review_chunks?: boolean;
  min_scene_s?: number;
  max_scene_s?: number;
  k_per_chunk?: number;
  caption_model?: string;
  auto_caption?: boolean;
  auto_export?: boolean;
}

export interface AutopilotRefinePayload {
  clip_sim_threshold?: number;
  phash_distance?: number;
  target_ratio?: number;
  purge_off_target?: boolean;
  refill?: boolean;
}

export interface AutopilotRefineResponse {
  status: string;
  video_id: string;
  stage: AutopilotStage;
  metrics: AutopilotMetrics;
}
