export interface PipelineSettings {
  default_k: number;
  default_rule: 'person' | 'equipment' | 'both' | 'either' | 'none' | string;
  sample_fps: number;
  survivor_frac: number;
  trigger_word: string;
  max_edge: number;
}
