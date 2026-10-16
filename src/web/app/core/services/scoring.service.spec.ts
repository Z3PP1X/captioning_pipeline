import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ScoringService } from './scoring.service';
import { SettingsService } from './settings.service';
import { ApiService } from './api.service';
import { ApiError } from './api-error';
import { LearnedWeightsStatus } from '../models/scoring.model';

function status(extra: Partial<LearnedWeightsStatus> = {}): LearnedWeightsStatus {
  return {
    weights: null,
    default_weights: { sharpness: 0.3 },
    n_events: 12,
    accuracy: null,
    fitted: false,
    min_events: 50,
    can_fit: false,
    use_learned_weights: false,
    active: false,
    ...extra,
  };
}

describe('ScoringService', () => {
  let service: ScoringService;
  let settings: SettingsService;
  let api: Record<'get' | 'post' | 'put', ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    api = { get: vi.fn(), post: vi.fn(), put: vi.fn() };
    TestBed.configureTestingModule({
      providers: [ScoringService, SettingsService, { provide: ApiService, useValue: api }],
    });
    service = TestBed.inject(ScoringService);
    settings = TestBed.inject(SettingsService);
  });

  it('loads the learned-weights status', async () => {
    api.get.mockResolvedValue(status({ n_events: 80, accuracy: 0.82, fitted: true, can_fit: true }));
    await service.loadLearnedWeights();
    expect(api.get).toHaveBeenCalledWith('/api/scoring/learned-weights');
    expect(service.learnedWeights()?.accuracy).toBe(0.82);
    expect(service.error()).toBeNull();
  });

  it('surfaces the 400 message when fitting with too few events', async () => {
    api.post.mockRejectedValue(new ApiError(400, 'Need at least 50 labelled events (have 12)', 'Need at least 50 labelled events (have 12)'));
    const ok = await service.fitLearnedWeights();
    expect(ok).toBe(false);
    expect(api.post).toHaveBeenCalledWith('/api/scoring/learned-weights/fit');
    expect(service.error()).toContain('at least 50');
    expect(service.isFitting()).toBe(false);
  });

  it('stores a fit result', async () => {
    api.post.mockResolvedValue(status({ fitted: true, accuracy: 0.9, can_fit: true }));
    expect(await service.fitLearnedWeights()).toBe(true);
    expect(service.learnedWeights()?.fitted).toBe(true);
  });

  it('toggles use_learned_weights with a partial PUT and refreshes the status', async () => {
    settings.settings.update((s) => ({ ...s, trigger_word: 'keep-me' }));
    api.put.mockResolvedValue({ settings: { ...settings.settings(), use_learned_weights: true } });
    api.get.mockResolvedValue(status({ use_learned_weights: true, active: true }));

    const ok = await service.setUseLearnedWeights(true);

    expect(ok).toBe(true);
    expect(api.put).toHaveBeenCalledWith('/api/settings', { use_learned_weights: true });
    expect(settings.settings().use_learned_weights).toBe(true);
    expect(settings.settings().trigger_word).toBe('keep-me');
    expect(service.learnedWeights()?.active).toBe(true);
  });

  it('keeps the old setting when the PUT fails', async () => {
    api.put.mockRejectedValue(new ApiError(400, { message: 'bad' }, 'bad'));
    const ok = await service.setUseLearnedWeights(true);
    expect(ok).toBe(false);
    expect(settings.settings().use_learned_weights).toBeUndefined();
    expect(service.error()).toBe('bad');
  });
});
