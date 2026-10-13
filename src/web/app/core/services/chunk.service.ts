import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { ChunkItem } from '../models/chunk.model';
import { FilmstripData } from '../models/video.model';

@Injectable({
  providedIn: 'root',
})
export class ChunkService {
  private readonly api = inject(ApiService);

  readonly chunks = signal<ChunkItem[]>([]);
  readonly filmstrip = signal<FilmstripData | null>(null);
  readonly cuts = signal<number[]>([]);
  readonly inPoint = signal<number | null>(null);
  readonly outPoint = signal<number | null>(null);
  readonly snapCuts = signal<boolean>(true);
  readonly isExtracting = signal<boolean>(false);
  readonly requestedSeekTime = signal<number | null>(null);

  readonly budgetSum = computed(() => {
    return Math.round(
      this.chunks().reduce((sum, c) => sum + (c.budget_pct || 0), 0)
    );
  });

  async loadChunks(videoId: string | number): Promise<void> {
    try {
      const data = await this.api.get<{ chunks?: ChunkItem[] }>(`/api/videos/${videoId}/chunks`);
      this.chunks.set(data.chunks || []);
    } catch (err) {
      console.error('Failed to load chunks:', err);
      this.chunks.set([]);
    }
  }

  async loadFilmstripAndCuts(videoId: string | number): Promise<void> {
    try {
      const data = await this.api.get<FilmstripData>(`/api/videos/${videoId}/filmstrip`);
      this.filmstrip.set(data);
      this.cuts.set(data.cuts || []);
    } catch (err) {
      console.error('Failed to load filmstrip & cuts:', err);
      this.filmstrip.set(null);
      this.cuts.set([]);
    }
  }

  addChunk(start: number, end: number): boolean {
    if (start >= end) return false;
    const current = this.chunks();
    if (current.length >= 15) {
      alert('Maximum 15 chunks allowed.');
      return false;
    }

    const newChunk: ChunkItem = {
      position: current.length,
      start_s: parseFloat(start.toFixed(2)),
      end_s: parseFloat(end.toFixed(2)),
      k: 5,
      budget_pct: 0,
      rule: 'person',
      notes: `Scene ${current.length + 1}`,
    };

    const updated = [...current, newChunk].sort((a, b) => a.start_s - b.start_s);
    updated.forEach((c, idx) => (c.position = idx));

    // Normalize budget percentages
    const count = updated.length;
    const equalShare = Math.floor(100 / count);
    const remainder = 100 % count;
    updated.forEach((c, idx) => {
      c.budget_pct = idx === 0 ? equalShare + remainder : equalShare;
    });

    this.chunks.set(updated);
    this.inPoint.set(null);
    this.outPoint.set(null);
    return true;
  }

  deleteChunk(index: number): void {
    const list = this.chunks().filter((_, i) => i !== index);
    list.forEach((c, i) => (c.position = i));
    this.chunks.set(list);
  }

  setAllModels(model: string): void {
    this.chunks.update((list) => list.map((c) => ({ ...c, caption_model: model })));
  }

  async updateChunkModel(chunkId: number | string, model: string): Promise<void> {
    try {
      await this.api.patch(`/api/chunks/${chunkId}`, { caption_model: model });
      this.chunks.update((list) =>
        list.map((c) => (String(c.id) === String(chunkId) || String(c.position) === String(chunkId) ? { ...c, caption_model: model } : c))
      );
    } catch (err) {
      console.error(`Failed to patch chunk ${chunkId} model:`, err);
      throw err;
    }
  }

  updateChunk(index: number, patch: Partial<ChunkItem>): void {
    this.chunks.update((list) => {
      const updated = [...list];
      if (updated[index]) {
        updated[index] = { ...updated[index], ...patch };
      }
      return updated;
    });
  }

  async autoSuggest(videoId: string | number): Promise<void> {
    try {
      const data = await this.api.post<{ chunks?: ChunkItem[]; suggested?: ChunkItem[] }>(
        `/api/videos/${videoId}/chunks/suggest`
      );
      this.chunks.set(data.suggested || data.chunks || []);
    } catch (err) {
      console.error('Failed to auto-suggest chunks:', err);
      throw err;
    }
  }

  async saveChunks(videoId: string | number): Promise<void> {
    try {
      const payload = { chunks: this.chunks() };
      await this.api.put(`/api/videos/${videoId}/chunks`, payload);
      await this.loadChunks(videoId);
    } catch (err) {
      console.error('Failed to save chunks:', err);
      throw err;
    }
  }

  async extract750(videoId: string | number): Promise<void> {
    try {
      this.isExtracting.set(true);
      await this.api.post(`/api/videos/${videoId}/extract-750`);
    } catch (err) {
      console.error('Failed to start 750 extraction:', err);
      throw err;
    } finally {
      this.isExtracting.set(false);
    }
  }

  snapTimeToCut(time: number, maxDistance = 0.5): number {
    if (!this.snapCuts()) return time;
    const cutList = this.cuts();
    for (const cut of cutList) {
      if (Math.abs(cut - time) <= maxDistance) {
        return cut;
      }
    }
    return time;
  }
}
