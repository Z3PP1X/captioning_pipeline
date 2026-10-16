import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { CurateService } from './curate.service';
import { ApiService } from './api.service';
import { CandidateFrame } from '../models/candidate.model';

describe('CurateService', () => {
  let service: CurateService;
  let mockApi: Partial<Record<keyof ApiService, ReturnType<typeof vi.fn>>>;

  const mockFrames: CandidateFrame[] = [
    {
      id: 1,
      video_id: 'vid-1',
      chunk_id: 10,
      time_s: 1.5,
      is_pick: true,
      state: 'keep',
      score: 0.9,
    },
    {
      id: 2,
      video_id: 'vid-1',
      chunk_id: 10,
      time_s: 3.0,
      is_pick: true,
      state: 'reject',
      score: 0.6,
    },
    {
      id: 3,
      video_id: 'vid-1',
      chunk_id: 20,
      time_s: 15.0,
      is_pick: true,
      state: 'star',
      score: 0.95,
    },
    {
      id: 4,
      video_id: 'vid-1',
      chunk_id: 20,
      time_s: 18.0,
      is_pick: false,
      state: null,
      score: 0.5,
    },
  ];

  beforeEach(() => {
    mockApi = {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        CurateService,
        { provide: ApiService, useValue: mockApi },
      ],
    });

    service = TestBed.inject(CurateService);
    service.candidates.set(mockFrames);
  });

  describe('filteredCandidates', () => {
    it('shows only picks by default when showRunnerUps is false', () => {
      service.showRunnerUps.set(false);
      service.curateRoleFilter.set('all');
      const list = service.filteredCandidates();
      expect(list.length).toBe(3);
      expect(list.every((c) => c.is_pick)).toBe(true);
    });

    it('shows all candidates when showRunnerUps is true', () => {
      service.showRunnerUps.set(true);
      service.curateRoleFilter.set('all');
      const list = service.filteredCandidates();
      expect(list.length).toBe(4);
    });

    it('filters by role (runner_up only)', () => {
      service.curateRoleFilter.set('runner_up');
      const list = service.filteredCandidates();
      expect(list.length).toBe(1);
      expect(list[0].id).toBe(4);
    });

    it('filters by chunk_id', () => {
      service.curateChunkFilter.set(10);
      const list = service.filteredCandidates();
      expect(list.length).toBe(2);
      expect(list.every((c) => c.chunk_id === 10)).toBe(true);
    });

    it('filters by state (star only)', () => {
      service.curateStateFilter.set('star');
      const list = service.filteredCandidates();
      expect(list.length).toBe(1);
      expect(list[0].id).toBe(3);
    });
  });

  describe('reselect', () => {
    it('posts the knobs and replaces the candidates from the curation-300-shaped response', async () => {
      const report = {
        target_count: 2, picks_count: 2, shortfall: 0, promoted: [4], demoted: [2], locked: 1, dup_skipped: 0,
        excluded_rejected: 0, diversity: 0.7, elasticity: 0.2, learned_weights_active: false, chunks: [],
      };
      mockApi.post!.mockResolvedValue({
        picks: [{ id: 3, chunk_id: 20, t: 15, state: 'star' }, { id: 4, chunk_id: 20, t: 18, state: 'keep' }],
        runners: [{ id: 1, chunk_id: 10, t: 1.5, state: 'candidate' }, { id: 2, chunk_id: 10, t: 3, state: 'reject' }],
        chunks: [{ id: 10, position: 0 }, { id: 20, position: 1 }],
        distribution: { multiple_pct: 40, one_person_pct: 30, tools_equipment_pct: 10, face_visible_pct: 60 },
        reselect: report,
      });

      const res = await service.reselect('vid-1', { target_count: 2, diversity: 0.7 });

      expect(mockApi.post).toHaveBeenCalledWith('/api/videos/vid-1/reselect', { target_count: 2, diversity: 0.7 });
      expect(res).toEqual(report);
      const list = service.candidates();
      expect(list.map((c) => [c.id, c.is_pick, c.role])).toEqual([
        [3, true, 'pick'],
        [4, true, 'pick'],
        [1, false, 'runner_up'],
        [2, false, 'runner_up'],
      ]);
      expect(list[1].time_s).toBe(18);
      expect(list[1].preview_url).toBe('/api/frames/4/preview');
      expect(service.curationChunks()).toHaveLength(2);
      expect(service.distribution()?.multiple_people?.pct).toBe(40);
      expect(service.distribution()?.selected_count).toBe(2);
    });

    it('leaves the list untouched and rethrows on 409', async () => {
      const err = Object.assign(new Error('busy'), { status: 409 });
      mockApi.post!.mockRejectedValue(err);
      await expect(service.reselect('vid-1', {})).rejects.toBe(err);
      expect(service.candidates()).toEqual(mockFrames);
    });
  });

  describe('lightbox navigation', () => {
    it('opens lightbox at correct candidate index', () => {
      service.openLightbox(3);
      expect(service.lightboxIndex()).toBe(2); // 3rd pick in default filtered list
      expect(service.currentLightboxCandidate()?.id).toBe(3);
    });

    it('navigates next and previous in lightbox', () => {
      service.openLightbox(1);
      expect(service.lightboxIndex()).toBe(0);

      service.nextLightbox();
      expect(service.lightboxIndex()).toBe(1);
      expect(service.currentLightboxCandidate()?.id).toBe(2);

      service.prevLightbox();
      expect(service.lightboxIndex()).toBe(0);
      expect(service.currentLightboxCandidate()?.id).toBe(1);
    });

    it('does not advance past the bounds of filtered candidates', () => {
      service.openLightbox(1);
      service.prevLightbox(); // already at 0
      expect(service.lightboxIndex()).toBe(0);

      service.nextLightbox(); // index 1
      service.nextLightbox(); // index 2 (last)
      service.nextLightbox(); // attempt past bounds
      expect(service.lightboxIndex()).toBe(2);
    });

    it('closes lightbox by setting lightboxIndex to null', () => {
      service.openLightbox(1);
      expect(service.currentLightboxCandidate()).not.toBeNull();

      service.closeLightbox();
      expect(service.lightboxIndex()).toBeNull();
      expect(service.currentLightboxCandidate()).toBeNull();
    });
  });
});
