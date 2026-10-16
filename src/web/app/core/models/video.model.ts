export interface WatermarkRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Video {
  id: string;
  name: string;
  path: string;
  slug: string;
  width: number;
  height: number;
  duration: number;
  fps: number;
  frame_count: number;
  broken: boolean;
  broken_reason?: string | null;
  status: 'new' | 'chunked' | 'extracting' | 'curating' | 'curating_300' | 'captioning' | 'exported' | 'skipped' | string;
  title_hash?: string | null;
  /** LoRA profile: 'character' gates picks on the target person, crops multi-person picks, captions in character mode. */
  profile?: 'concept' | 'character';
  watermark_rect?: WatermarkRect | null;
  letterbox?: Record<string, number> | null;
  chunk_count?: number;
  export_count?: number;
  kept_count?: number;
  captioned_count?: number;
  target_person_id?: number | null;
  target_person_name?: string | null;
  positive_persons?: Array<{ id: number; name: string; face_crop_path?: string | null; face_crop_url?: string | null }>;
  negative_persons?: Array<{ id: number; name: string; face_crop_path?: string | null; face_crop_url?: string | null }>;
  positive_person_ids?: number[];
  negative_person_ids?: number[];
  autopilot?: Record<string, any> | null;
  autopilot_stage?: string | null;
}

export interface FilmstripThumbnail {
  time_s: number;
  url: string;
}

export interface FilmstripData {
  video_id: string;
  duration: number;
  thumbnails: FilmstripThumbnail[];
  cuts: number[];
}
