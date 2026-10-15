export interface PipelineSettings {
  default_k: number;
  default_rule: 'person' | 'equipment' | 'both' | 'either' | 'none' | string;
  sample_fps: number;
  survivor_frac: number;
  trigger_word: string;
  max_edge: number;
  snap_to_cuts?: boolean;
  snap_window_s?: number;
  suggest_min_len_s?: number;
}
