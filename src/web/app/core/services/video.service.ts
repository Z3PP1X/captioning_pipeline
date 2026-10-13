import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { Video, WatermarkRect } from '../models/video.model';

@Injectable({
  providedIn: 'root',
})
export class VideoService {
  private readonly api = inject(ApiService);

  readonly videos = signal<Video[]>([]);
  readonly currentVideo = signal<Video | null>(null);
  readonly searchQuery = signal<string>('');
  readonly statusFilter = signal<string>('');
  readonly isLoading = signal<boolean>(false);

  readonly filteredVideos = computed(() => {
    const search = this.searchQuery().trim().toLowerCase();
    const status = this.statusFilter();
    let list = this.videos();

    if (status) {
      if (status === 'curating') {
        list = list.filter((v) => v.status === 'curating' || v.status === 'curating_300');
      } else if (status === 'extracting') {
        list = list.filter((v) => v.status === 'extracting' || v.status === 'extracting_750');
      } else {
        list = list.filter((v) => v.status === status);
      }
    }
    if (search) {
      list = list.filter((v) => v.name.toLowerCase().includes(search));
    }
    return list;
  });

  async loadVideos(): Promise<void> {
    try {
      this.isLoading.set(true);
      const queryParams = new URLSearchParams();
      if (this.searchQuery().trim()) {
        queryParams.set('search', this.searchQuery().trim());
      }
      if (this.statusFilter()) {
        queryParams.set('status', this.statusFilter());
      }
      const data = await this.api.get<{ videos: Video[] }>(`/api/videos?${queryParams.toString()}`);
      this.videos.set(data.videos || []);

      // If current video is set, update it with fresh data
      const current = this.currentVideo();
      if (current) {
        const found = data.videos.find((v) => v.id === current.id);
        if (found) {
          this.currentVideo.set(found);
        }
      }
    } catch (err) {
      console.error('Failed to load videos:', err);
    } finally {
      this.isLoading.set(false);
    }
  }

  async selectVideo(id: string | number): Promise<Video | null> {
    const strId = String(id);
    const list = this.videos();
    let video = list.find((v) => String(v.id) === strId);
    if (!video) {
      try {
        const data = await this.api.get<{ video: Video }>(`/api/videos/${strId}`);
        video = data.video;
      } catch (e) {
        console.error(`Failed to fetch video ${strId}:`, e);
      }
    }

    if (video) {
      this.currentVideo.set(video);
      return video;
    }
    return null;
  }

  async selectNextVideo(): Promise<Video | null> {
    const list = this.videos();
    const current = this.currentVideo();
    const curIdx = current ? list.findIndex((v) => v.id === current.id) : -1;

    // First try to find next unprocessed/non-exported video after current
    for (let i = curIdx + 1; i < list.length; i++) {
      if (!list[i].broken && list[i].status !== 'exported') {
        return this.selectVideo(list[i].id);
      }
    }

    // Wrap around to start if not found
    for (let i = 0; i <= curIdx; i++) {
      if (!list[i].broken && list[i].status !== 'exported') {
        return this.selectVideo(list[i].id);
      }
    }

    // Fallback: next video in list or first video
    if (list.length > 0) {
      const nextIdx = (curIdx + 1) % list.length;
      return this.selectVideo(list[nextIdx].id);
    }

    return null;
  }

  async rescan(): Promise<void> {
    try {
      this.isLoading.set(true);
      await this.api.post('/api/videos/scan');
      await this.loadVideos();
    } catch (err) {
      console.error('Failed to rescan video library:', err);
    } finally {
      this.isLoading.set(false);
    }
  }

  async syncCaptions(): Promise<void> {
    try {
      this.isLoading.set(true);
      await this.api.post('/api/videos/sync-captions');
      await this.loadVideos();
    } catch (err) {
      console.error('Failed to sync captions with finished folder:', err);
    } finally {
      this.isLoading.set(false);
    }
  }

  async updateStatus(videoId: string | number, status: string): Promise<void> {
    const strId = String(videoId);
    try {
      await this.api.patch(`/api/videos/${strId}`, { status });
      const cur = this.currentVideo();
      if (cur && String(cur.id) === strId) {
        this.currentVideo.set({ ...cur, status });
      }
      this.videos.update((list) =>
        list.map((v) => (String(v.id) === strId ? { ...v, status } : v))
      );
    } catch (err) {
      console.error('Failed to update video status:', err);
      throw err;
    }
  }

  async saveWatermark(videoId: string | number, rect: WatermarkRect | null): Promise<void> {
    const strId = String(videoId);
    try {
      await this.api.patch(`/api/videos/${strId}`, { watermark_rect: rect });
      const cur = this.currentVideo();
      if (cur && String(cur.id) === strId) {
        this.currentVideo.set({ ...cur, watermark_rect: rect });
      }
      this.videos.update((list) =>
        list.map((v) => (String(v.id) === strId ? { ...v, watermark_rect: rect } : v))
      );
    } catch (err) {
      console.error('Failed to save watermark rectangle:', err);
      throw err;
    }
  }
}
