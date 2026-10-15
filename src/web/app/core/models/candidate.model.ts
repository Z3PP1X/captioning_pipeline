export interface ScoringMetrics {
  blur_laplacian?: number;
  color_entropy?: number;
  edge_density?: number;
  face_score?: number;
  person_score?: number;
  equipment_score?: number;
  final_score?: number;
  [key: string]: number | undefined;
}

export type CandidateState = 'keep' | 'reject' | 'star' | null;

export interface CandidateFrame {
  id: number;
  video_id: string | number;
  chunk_id: number;
  t?: number;
  time_s: number;
  pts?: number;
  role?: 'pick' | 'runner_up' | string;
  is_pick: boolean;
  rank?: number;
  state?: CandidateState;
  score?: number;
  metrics?: ScoringMetrics;
  zs_scores?: Record<string, any>;
  categories?: {
    multiple_people?: boolean;
    one_person?: boolean;
    tools_equipment?: boolean;
    face_visible?: boolean;
  };
  thumb_url?: string;
  preview_url?: string;
  full_url?: string;
  is_captioned?: boolean;
  caption_text?: string;
  chunk_position?: number;
  chunk_pos?: number;
  chunk_start?: number;
  chunk_end?: number;
  chunk_tags?: string[] | string;
  budget_pct?: number;
  caption_model?: string;
  target_person_sim?: number;
  target_person_matched?: boolean;
  face_count?: number;
  faces?: Array<{ box: [number, number, number, number]; score: number; sim: number; matched: boolean }>;
}

export interface DistributionStat {
  pct: number;
  target_pct?: number;
  count?: number;
}

export interface CurationDistribution {
  multiple_people?: DistributionStat;
  one_person?: DistributionStat;
  tools_equipment?: DistributionStat;
  face_visible?: DistributionStat;
  target_person?: DistributionStat;
  target_person_pct?: number;
  selected_count?: number;
  target_count?: number;
}
