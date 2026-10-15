import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ChunkService } from './chunk.service';
import { ApiService } from './api.service';
import { ChunkItem } from '../models/chunk.model';

describe('ChunkService', () => {
  let service: ChunkService;
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
  });

  describe('addChunk', () => {
    it('returns false if start >= end', () => {
      expect(service.addChunk(10, 5)).toBe(false);
      expect(service.addChunk(10, 10)).toBe(false);
      expect(service.chunks().length).toBe(0);
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

    it('caps maximum chunks at 15', () => {
      for (let i = 0; i < 15; i++) {
        expect(service.addChunk(i * 10, (i + 1) * 10)).toBe(true);
      }
      expect(service.chunks().length).toBe(15);
      expect(service.addChunk(150, 160)).toBe(false);
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
