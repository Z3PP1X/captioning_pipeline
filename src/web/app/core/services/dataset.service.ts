import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { DatasetMetrics, ExportVideoResult, ExportedFramesState } from '../models/dataset.model';
import { CandidateFrame } from '../models/candidate.model';

@Injectable({
  providedIn: 'root',
})
export class DatasetService {
  private readonly api = inject(ApiService);

  readonly metrics = signal<DatasetMetrics>({
    total_exports: 0,
    per_video: [],
    finished_dir: '',
  });
  readonly isLoading = signal<boolean>(false);
  /** Exported frames per video id (Dataset row "Frames"), for "Use as prompt seed" / "More like this frame". */
  readonly exportedFrames = signal<Record<string, ExportedFramesState>>({});

  /**
   * `GET /api/dataset` has no frame ids, so the exported frames come from the video's curation-300 rows
   * (`is_captioned` = the frame has an exports row).
   */
  async loadExportedFrames(videoId: string | number): Promise<void> {
    const key = String(videoId);
    const set = (state: ExportedFramesState) => this.exportedFrames.update((m) => ({ ...m, [key]: state }));
    set({ loading: true, frames: this.exportedFrames()[key]?.frames ?? [], error: null });
    try {
      const res = await this.api.get<{ picks?: CandidateFrame[]; runners?: CandidateFrame[] }>(
        `/api/videos/${encodeURIComponent(key)}/curation-300`,
      );
      const frames = [...(res.picks || []), ...(res.runners || [])]
        .filter((f) => f.is_captioned)
        .map((f) => ({ ...f, time_s: f.t ?? f.time_s ?? 0, preview_url: f.preview_url || `/api/frames/${f.id}/preview` }))
        .sort((a, b) => a.time_s - b.time_s);
      set({ loading: false, frames, error: null });
    } catch (err) {
      set({ loading: false, frames: [], error: err instanceof Error ? err.message : String(err) });
    }
  }

  async loadDataset(): Promise<void> {
    try {
      this.isLoading.set(true);
      const data = await this.api.get<DatasetMetrics>('/api/dataset');
      this.metrics.set(data);
    } catch (err) {
      console.error('Failed to load dataset metrics:', err);
    } finally {
      this.isLoading.set(false);
    }
  }

  async exportVideo(videoId: string | number): Promise<ExportVideoResult> {
    try {
      const res = await this.api.post<ExportVideoResult>('/api/export', { video_id: String(videoId) });
      await this.loadDataset();
      return res;
    } catch (err) {
      console.error(`Failed to export video ${videoId}:`, err);
      throw err;
    }
  }

  async exportAllCurated(): Promise<void> {
    try {
      this.isLoading.set(true);
      await this.api.post('/api/export', {});
      await this.loadDataset();
    } catch (err) {
      console.error('Failed to export all curated videos:', err);
      throw err;
    } finally {
      this.isLoading.set(false);
    }
  }
}
