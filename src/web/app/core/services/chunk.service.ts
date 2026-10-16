import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { ChunkItem } from '../models/chunk.model';
import { FilmstripData } from '../models/video.model';
import { ChunkPriority, PRIORITY_WEIGHTS } from './autopilot.service';
import { SettingsService } from './settings.service';

/** Maximum number of chunks per video (spec: "max 15 chunks"). */
export const MAX_CHUNKS = 15;
/** Snap-to-cut radius used when `settings.snap_window_s` is not set (spec default). */
export const DEFAULT_SNAP_WINDOW_S = 1.5;

/**
 * Even budget split per spec: `floor(100 / n)` each, the remainder distributed +1 to the first chunks
 * (e.g. 3 chunks -> 34/33/33, 7 chunks -> 15/15/14/14/14/14/14).
 */
export function evenBudgetSplit(count: number): number[] {
  if (count <= 0) return [];
  const share = Math.floor(100 / count);
  const remainder = 100 % count;
  return Array.from({ length: count }, (_, idx) => share + (idx < remainder ? 1 : 0));
}

export type AddChunkResult = 'added' | 'invalid_range' | 'max_reached';

@Injectable({
  providedIn: 'root',
})
export class ChunkService {
  private readonly api = inject(ApiService);
  private readonly settingsService = inject(SettingsService);

  readonly chunks = signal<ChunkItem[]>([]);
  readonly filmstrip = signal<FilmstripData | null>(null);
  readonly cuts = signal<number[]>([]);
  readonly inPoint = signal<number | null>(null);
  readonly outPoint = signal<number | null>(null);
  readonly snapCuts = signal<boolean>(true);
  readonly isExtracting = signal<boolean>(false);
  readonly extractingChunkIds = signal<Set<number>>(new Set());
  readonly requestedSeekTime = signal<number | null>(null);
  readonly isDirty = signal<boolean>(false);

  readonly budgetSum = computed(() => {
    return Math.round(
      this.chunks().reduce((sum, c) => sum + (c.budget_pct || 0), 0)
    );
  });

  setChunkPriority(index: number, priority: ChunkPriority): void {
    const list = this.chunks();
    if (index < 0 || index >= list.length) return;

    const updated = list.map((c, i) => (i === index ? { ...c, priority } : { ...c }));
    const totalWeight = updated.reduce((sum, c) => {
      const p = (c.priority || 'normal') as ChunkPriority;
      return sum + (p === 'skip' ? 0 : (PRIORITY_WEIGHTS[p] ?? 1.0));
    }, 0);

    if (totalWeight > 0) {
      let allocated = 0;
      const nonSkipIndices = updated
        .map((c, i) => (c.priority !== 'skip' ? i : -1))
        .filter((i) => i !== -1);
      const lastNonSkipIdx = nonSkipIndices[nonSkipIndices.length - 1];

      updated.forEach((c, i) => {
        const p = (c.priority || 'normal') as ChunkPriority;
        if (p === 'skip') {
          c.budget_pct = 0;
        } else if (i === lastNonSkipIdx) {
          c.budget_pct = Math.max(0, 100 - allocated);
        } else {
          const w = PRIORITY_WEIGHTS[p] ?? 1.0;
          const pct = Math.round((w / totalWeight) * 100);
          c.budget_pct = pct;
          allocated += pct;
        }
      });
    } else {
      updated.forEach((c) => (c.budget_pct = 0));
    }

    this.chunks.set(updated);
    this.isDirty.set(true);
  }

  normalizeBudgets(): void {
    const list = this.chunks();
    if (list.length === 0) return;
    const count = list.length;
    const currentSum = this.budgetSum();
    if (currentSum === 100) return;

    if (currentSum === 0) {
      const split = evenBudgetSplit(count);
      const updated = list.map((c, idx) => ({ ...c, budget_pct: split[idx] }));
      this.chunks.set(updated);
      this.isDirty.set(true);
      return;
    }

    let allocated = 0;
    const updated = list.map((c, idx) => {
      if (idx === count - 1) {
        return { ...c, budget_pct: Math.max(0, 100 - allocated) };
      }
      const pct = Math.max(0, Math.round(((c.budget_pct || 0) / currentSum) * 100));
      allocated += pct;
      return { ...c, budget_pct: pct };
    });
    this.chunks.set(updated);
    this.isDirty.set(true);
  }

  async loadChunks(videoId: string | number): Promise<void> {
    try {
      const data = await this.api.get<{ chunks?: ChunkItem[] }>(`/api/videos/${videoId}/chunks`);
      this.chunks.set(data.chunks || []);
      this.isDirty.set(false);
    } catch (err) {
      console.error('Failed to load chunks:', err);
      this.chunks.set([]);
      this.isDirty.set(false);
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

  /** Adds a chunk; In/Out may be given in either order (the range uses min/max per spec). */
  addChunk(inPoint: number, outPoint: number): boolean {
    return this.tryAddChunk(inPoint, outPoint) === 'added';
  }

  tryAddChunk(inPoint: number, outPoint: number): AddChunkResult {
    const start = Math.min(inPoint, outPoint);
    const end = Math.max(inPoint, outPoint);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end - start <= 0) return 'invalid_range';
    const current = this.chunks();
    if (current.length >= MAX_CHUNKS) {
      return 'max_reached';
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

    // Re-normalise budgets: even split, remainder +1 to the first chunks
    const split = evenBudgetSplit(updated.length);
    updated.forEach((c, idx) => (c.budget_pct = split[idx]));

    this.chunks.set(updated);
    this.inPoint.set(null);
    this.outPoint.set(null);
    this.isDirty.set(true);
    return 'added';
  }

  deleteChunk(index: number): void {
    const list = this.chunks().filter((_, i) => i !== index);
    list.forEach((c, i) => (c.position = i));
    this.chunks.set(list);
    this.isDirty.set(true);
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
    this.isDirty.set(true);
  }

  /** `confirmDestructive` allows replacing chunks that hold picks (the backend answers 409 otherwise). */
  async autoSuggest(videoId: string | number, confirmDestructive = false): Promise<void> {
    try {
      const data = await this.api.post<{ chunks?: ChunkItem[]; suggested?: ChunkItem[] }>(
        `/api/videos/${videoId}/chunks/suggest`,
        { confirm_destructive: confirmDestructive }
      );
      this.chunks.set(data.suggested || data.chunks || []);
      this.isDirty.set(true);
    } catch (err) {
      console.error('Failed to auto-suggest chunks:', err);
      throw err;
    }
  }

  /** `confirmDestructive` allows deleting chunks that hold picks (the backend answers 409 otherwise). */
  async saveChunks(videoId: string | number, confirmDestructive = false): Promise<void> {
    try {
      const payload = { chunks: this.chunks(), confirm_destructive: confirmDestructive };
      await this.api.put(`/api/videos/${videoId}/chunks`, payload);
      await this.loadChunks(videoId);
      this.isDirty.set(false);
    } catch (err) {
      console.error('Failed to save chunks:', err);
      throw err;
    }
  }

  async extract750(videoId: string | number): Promise<void> {
    try {
      this.isExtracting.set(true);
      if (this.isDirty() || this.budgetSum() !== 100) {
        this.normalizeBudgets();
        await this.saveChunks(videoId, false);
      }
      await this.api.post(`/api/videos/${videoId}/extract-750`);
    } catch (err) {
      console.error('Failed to start 750 extraction:', err);
      throw err;
    } finally {
      this.isExtracting.set(false);
    }
  }

  isExtractingChunk(chunkId: number | null | undefined): boolean {
    if (chunkId === undefined || chunkId === null) return false;
    return this.extractingChunkIds().has(chunkId);
  }

  /**
   * Multi-step targeted extraction: extracts new candidates and picks strictly within this chunk's timecode.
   * Preserves all other chunks and existing curations.
   */
  async extractChunk(
    chunkId: number,
    options?: { k?: number; budget_pct?: number }
  ): Promise<{ job_id: number; status: string }> {
    try {
      this.extractingChunkIds.update((set) => {
        const next = new Set(set);
        next.add(chunkId);
        return next;
      });
      const res = await this.api.post<{ job_id: number; status: string; chunk_id: number; video_id: string }>(
        `/api/chunks/${chunkId}/extract`,
        options || {}
      );
      return res;
    } catch (err) {
      console.error(`Failed to extract chunk #${chunkId}:`, err);
      throw err;
    } finally {
      this.extractingChunkIds.update((set) => {
        const next = new Set(set);
        next.delete(chunkId);
        return next;
      });
    }
  }

  /** Current snap radius: `settings.snap_window_s` (default 1.5 s). */
  snapWindow(): number {
    const w = this.settingsService.settings().snap_window_s;
    return typeof w === 'number' && Number.isFinite(w) && w >= 0 ? w : DEFAULT_SNAP_WINDOW_S;
  }

  /** Snaps to the nearest scene cut within the snap window (when Snap Cuts is on). */
  snapTimeToCut(time: number, maxDistance: number = this.snapWindow()): number {
    if (!this.snapCuts()) return time;
    let best = time;
    let bestDist = Infinity;
    for (const cut of this.cuts()) {
      const dist = Math.abs(cut - time);
      if (dist <= maxDistance && dist < bestDist) {
        best = cut;
        bestDist = dist;
      }
    }
    return best;
  }
}
