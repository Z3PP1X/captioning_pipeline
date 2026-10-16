import { CandidateFrame } from './candidate.model';

export interface ExportedVideoItem {
  id: number;
  name: string;
  slug: string;
  count: number;
}

/** Exported frames of one video (frames with an `exports` row), loaded on demand in the Dataset view. */
export interface ExportedFramesState {
  loading: boolean;
  frames: CandidateFrame[];
  error: string | null;
}

export interface DatasetMetrics {
  total_exports: number;
  per_video: ExportedVideoItem[];
  finished_dir: string;
}

/** `POST /api/export {video_id}` result (src/pipeline/export.py `export_video`). */
export interface ExportVideoResult {
  exported: number;
  stubs_written?: number;
  captions_kept?: number;
  per_video_dir?: string;
  merged_dir?: string;
  /** Set instead of the counts when nothing could be exported. */
  error?: string;
  msg?: string;
}
