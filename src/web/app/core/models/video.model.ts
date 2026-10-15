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
  watermark_rect?: WatermarkRect | null;
  letterbox?: Record<string, number> | null;
  chunk_count?: number;
  export_count?: number;
  kept_count?: number;
  captioned_count?: number;
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
