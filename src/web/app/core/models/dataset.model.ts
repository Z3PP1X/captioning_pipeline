export interface ExportedVideoItem {
  id: number;
  name: string;
  slug: string;
  count: number;
}

export interface DatasetMetrics {
  total_exports: number;
  per_video: ExportedVideoItem[];
  finished_dir: string;
}
