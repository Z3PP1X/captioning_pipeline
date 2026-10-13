import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { DatasetMetrics } from '../models/dataset.model';

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

  async exportVideo(videoId: string | number): Promise<void> {
    try {
      await this.api.post('/api/export', { video_id: String(videoId) });
      await this.loadDataset();
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
