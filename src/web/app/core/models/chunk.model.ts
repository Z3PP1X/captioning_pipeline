export interface ChunkItem {
  id?: number | null;
  position: number;
  start_s: number;
  end_s: number;
  k: number;
  budget_pct: number;
  caption_model?: string;
  rule: 'person' | 'equipment' | 'both' | 'either' | 'none' | string;
  tags?: string[];
  notes?: string;
  captioned_count?: number;
  picks_count?: number;
  exports_count?: number;
  total_frames?: number;
  is_fully_captioned?: boolean;
  caption_progress_pct?: number;
}

export interface ChunksUpdatePayload {
  chunks: ChunkItem[];
}
