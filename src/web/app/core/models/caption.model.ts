/** Row of the `captions` table (Phase G source of truth), `GET /api/frames/{id}/caption`. */
export interface CaptionRow {
  frame_id: number;
  video_id: string;
  text: string;
  model: string | null;
  layer: 'layer1' | 'layer2' | 'layer3' | string | null;
  mode: string | null;
  prompt_version: string | null;
  trigger: string | null;
  word_count: number | null;
  cost_usd: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  warnings: string[];
  finish_reason: string | null;
  job_id: number | null;
  created_at: number | string | null;
  source: 'vlm' | 'imported' | 'stub';
}

export interface FrameCaptionResponse {
  frame_id: number;
  caption: CaptionRow | null;
}
