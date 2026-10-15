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
