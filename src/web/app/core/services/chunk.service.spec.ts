import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ChunkService, evenBudgetSplit, MAX_CHUNKS } from './chunk.service';
import { ApiService } from './api.service';
import { SettingsService } from './settings.service';
import { ChunkItem } from '../models/chunk.model';

describe('ChunkService', () => {
  let service: ChunkService;
  let settings: SettingsService;
  let mockApi: Partial<Record<keyof ApiService, ReturnType<typeof vi.fn>>>;

  beforeEach(() => {
    mockApi = {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        ChunkService,
        { provide: ApiService, useValue: mockApi },
      ],
    });

    service = TestBed.inject(ChunkService);
    settings = TestBed.inject(SettingsService);
  });

  describe('evenBudgetSplit', () => {
    it('gives floor(100/n) to every chunk and +1 to the first (100 % n) chunks', () => {
      expect(evenBudgetSplit(1)).toEqual([100]);
      expect(evenBudgetSplit(3)).toEqual([34, 33, 33]);
      expect(evenBudgetSplit(6)).toEqual([17, 17, 17, 17, 16, 16]);
      expect(evenBudgetSplit(7)).toEqual([15, 15, 14, 14, 14, 14, 14]);
      expect(evenBudgetSplit(0)).toEqual([]);
    });

    it('always sums to exactly 100 for 1..15 chunks', () => {
      for (let n = 1; n <= MAX_CHUNKS; n++) {
        const split = evenBudgetSplit(n);
        expect(split.reduce((a, b) => a + b, 0)).toBe(100);
        expect(Math.max(...split) - Math.min(...split)).toBeLessThanOrEqual(1);
      }
    });
  });

  describe('addChunk', () => {
    it('returns false for an empty range', () => {
      expect(service.addChunk(10, 10)).toBe(false);
      expect(service.tryAddChunk(10, 10)).toBe('invalid_range');
      expect(service.chunks().length).toBe(0);
    });

    it('accepts Out before In by using min/max', () => {
      expect(service.addChunk(10, 5)).toBe(true);
      expect(service.chunks()[0].start_s).toBe(5);
      expect(service.chunks()[0].end_s).toBe(10);
    });

    it('adds chunk, normalizes budget and sets isDirty to true', () => {
      const added = service.addChunk(0, 10);
      expect(added).toBe(true);
      expect(service.chunks().length).toBe(1);
      expect(service.chunks()[0].budget_pct).toBe(100);
      expect(service.budgetSum()).toBe(100);
      expect(service.isDirty()).toBe(true);
    });

    it('splits budget evenly across multiple chunks summing to 100%', () => {
      service.addChunk(0, 10);
      service.addChunk(10, 20);
      service.addChunk(20, 30);

      expect(service.chunks().length).toBe(3);
      expect(service.budgetSum()).toBe(100);
      expect(service.chunks()[0].budget_pct).toBe(34); // equal share 33 + remainder 1
      expect(service.chunks()[1].budget_pct).toBe(33);
      expect(service.chunks()[2].budget_pct).toBe(33);
    });

    it('distributes the remainder +1 to the first chunks when adding', () => {
      for (let i = 0; i < 7; i++) service.addChunk(i * 10, (i + 1) * 10);
      expect(service.chunks().map((c) => c.budget_pct)).toEqual([15, 15, 14, 14, 14, 14, 14]);
      expect(service.budgetSum()).toBe(100);
    });

    it('caps maximum chunks at 15 and reports max_reached', () => {
      for (let i = 0; i < 15; i++) {
        expect(service.addChunk(i * 10, (i + 1) * 10)).toBe(true);
      }
      expect(service.chunks().length).toBe(15);
      expect(service.addChunk(150, 160)).toBe(false);
      expect(service.tryAddChunk(150, 160)).toBe('max_reached');
      expect(service.chunks().length).toBe(15);
    });
  });

  describe('normalizeBudgets', () => {
    it('normalizes skewed percentages to sum to exactly 100%', () => {
      const raw: ChunkItem[] = [
        { position: 0, start_s: 0, end_s: 10, k: 5, budget_pct: 10, rule: 'person' },
        { position: 1, start_s: 10, end_s: 20, k: 5, budget_pct: 10, rule: 'person' },
      ];
      service.chunks.set(raw);
      expect(service.budgetSum()).toBe(20);

      service.normalizeBudgets();
      expect(service.budgetSum()).toBe(100);
      expect(service.chunks()[0].budget_pct).toBe(50);
      expect(service.chunks()[1].budget_pct).toBe(50);
      expect(service.isDirty()).toBe(true);
    });

    it('distributes a zero sum evenly with the remainder on the first chunks', () => {
      service.chunks.set(
        [0, 1, 2].map((i) => ({ position: i, start_s: i * 10, end_s: i * 10 + 10, k: 5, budget_pct: 0, rule: 'person' }))
      );
      service.normalizeBudgets();
      expect(service.chunks().map((c) => c.budget_pct)).toEqual([34, 33, 33]);
    });

    it('handles zero sum by distributing equally', () => {
      const raw: ChunkItem[] = [
        { position: 0, start_s: 0, end_s: 10, k: 5, budget_pct: 0, rule: 'person' },
        { position: 1, start_s: 10, end_s: 20, k: 5, budget_pct: 0, rule: 'person' },
      ];
      service.chunks.set(raw);
      service.normalizeBudgets();
      expect(service.budgetSum()).toBe(100);
      expect(service.chunks()[0].budget_pct).toBe(50);
      expect(service.chunks()[1].budget_pct).toBe(50);
    });
  });

  describe('snapTimeToCut', () => {
    beforeEach(() => {
      service.cuts.set([5.0, 12.34, 25.0]);
    });

    it('snaps to nearest cut within maxDistance if snapCuts is true', () => {
      service.snapCuts.set(true);
      expect(service.snapTimeToCut(5.2, 0.5)).toBe(5.0);
      expect(service.snapTimeToCut(12.5, 0.5)).toBe(12.34);
    });

    it('does not snap if distance exceeds maxDistance', () => {
      service.snapCuts.set(true);
      expect(service.snapTimeToCut(5.8, 0.5)).toBe(5.8);
      expect(service.snapTimeToCut(10.0, 0.5)).toBe(10.0);
    });

    it('does not snap if snapCuts is false', () => {
      service.snapCuts.set(false);
      expect(service.snapTimeToCut(5.1, 0.5)).toBe(5.1);
    });

    it('uses settings.snap_window_s as the default radius', () => {
      service.snapCuts.set(true);
      settings.settings.update((s) => ({ ...s, snap_window_s: 1.5 }));
      expect(service.snapWindow()).toBe(1.5);
      expect(service.snapTimeToCut(6.2)).toBe(5.0); // 1.2 s away: inside 1.5 s
      expect(service.snapTimeToCut(6.8)).toBe(6.8); // 1.8 s away: outside

      settings.settings.update((s) => ({ ...s, snap_window_s: 0.25 }));
      expect(service.snapTimeToCut(5.2)).toBe(5.0);
      expect(service.snapTimeToCut(5.4)).toBe(5.4);
    });

    it('falls back to 1.5 s when snap_window_s is missing', () => {
      settings.settings.update((s) => ({ ...s, snap_window_s: undefined }));
      expect(service.snapWindow()).toBe(1.5);
      expect(service.snapTimeToCut(3.6)).toBe(5.0);
    });

    it('snaps to the nearest cut, not the first one in range', () => {
      service.snapCuts.set(true);
      service.cuts.set([10.0, 11.0]);
      expect(service.snapTimeToCut(10.8, 1.5)).toBe(11.0);
    });
  });

  describe('deleteChunk', () => {
    it('removes chunk and re-indexes positions', () => {
      service.addChunk(0, 10);
      service.addChunk(10, 20);
      service.addChunk(20, 30);

      service.deleteChunk(1);
      expect(service.chunks().length).toBe(2);
      expect(service.chunks()[0].position).toBe(0);
      expect(service.chunks()[1].position).toBe(1);
      expect(service.chunks()[1].start_s).toBe(20);
      expect(service.isDirty()).toBe(true);
    });
  });
});
