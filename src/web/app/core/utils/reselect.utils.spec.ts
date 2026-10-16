import { describe, it, expect } from 'vitest';
import {
  ReselectFormModel,
  buildReselectRequest,
  formatReselectSummary,
  reselectErrorMessage,
  reselectFormErrors,
} from './reselect.utils';
import { ApiError } from '../services/api-error';
import { ReselectReport } from '../models/scoring.model';

const base: ReselectFormModel = {
  target_count: 180,
  diversity: 0.5,
  elasticity: 0.2,
  per_chunk_floor: null,
  per_chunk_cap: null,
};

function report(extra: Partial<ReselectReport> = {}): ReselectReport {
  return {
    target_count: 180,
    picks_count: 180,
    shortfall: 0,
    promoted: [1, 2, 3],
    demoted: [4, 5],
    locked: 7,
    dup_skipped: 0,
    excluded_rejected: 2,
    diversity: 0.5,
    elasticity: 0.2,
    learned_weights_active: false,
    chunks: [],
    ...extra,
  };
}

describe('reselectFormErrors', () => {
  it('accepts defaults and empty optional knobs', () => {
    expect(reselectFormErrors(base, 5)).toEqual({});
    expect(reselectFormErrors({ ...base, target_count: null, diversity: null, elasticity: null }, 5)).toEqual({});
  });

  it('flags out-of-range, non-integer and NaN values with the server ranges', () => {
    const errors = reselectFormErrors({ ...base, target_count: 0, diversity: 1.5, elasticity: Number.NaN, per_chunk_floor: 2.5 });
    expect(errors.target_count).toContain('between 1 and 5000');
    expect(errors.diversity).toContain('between 0 and 1');
    expect(errors.elasticity).toContain('must be a number');
    expect(errors.per_chunk_floor).toContain('whole number');
  });

  it('rejects a floor above the cap and floors that exceed the target', () => {
    expect(reselectFormErrors({ ...base, per_chunk_floor: 10, per_chunk_cap: 5 }).per_chunk_cap).toContain('at least the floor');
    expect(reselectFormErrors({ ...base, target_count: 20, per_chunk_floor: 5 }, 5).per_chunk_floor).toContain('at least 25 picks');
    expect(reselectFormErrors({ ...base, target_count: 25, per_chunk_floor: 5 }, 5)).toEqual({});
  });
});

describe('buildReselectRequest', () => {
  it('omits empty knobs so the server applies its defaults', () => {
    expect(buildReselectRequest({ ...base, elasticity: null, per_chunk_cap: 40 })).toEqual({
      target_count: 180,
      diversity: 0.5,
      per_chunk_cap: 40,
    });
  });

  it('keeps legitimate zeros', () => {
    expect(buildReselectRequest({ ...base, diversity: 0, per_chunk_floor: 0 })).toMatchObject({ diversity: 0, per_chunk_floor: 0 });
  });
});

describe('formatReselectSummary', () => {
  it('reports promoted / demoted / locked', () => {
    const s = formatReselectSummary(report());
    expect(s.title).toBe('Picks reselected');
    expect(s.message).toContain('180 of 180 picks');
    expect(s.message).toContain('3 promoted, 2 demoted, 7 locked');
    expect(s.isWarning).toBe(false);
  });

  it('warns about a shortfall and mentions learned weights', () => {
    const s = formatReselectSummary(report({ picks_count: 170, shortfall: 10, learned_weights_active: true, dup_skipped: 4 }));
    expect(s.title).toBe('Reselected with a shortfall');
    expect(s.message).toContain('Shortfall: 10');
    expect(s.message).toContain('4 near-duplicates skipped');
    expect(s.message).toContain('Learned weights');
    expect(s.isWarning).toBe(true);
  });
});

describe('reselectErrorMessage', () => {
  it('maps the API statuses to readable text', () => {
    expect(reselectErrorMessage(new ApiError(409, 'busy', 'busy'))).toContain('queued or running job');
    expect(reselectErrorMessage(new ApiError(400, 'floors above target', 'floors above target'))).toContain('floors above target');
    expect(reselectErrorMessage(new ApiError(422, [], 'x'))).toContain('out of range');
    expect(reselectErrorMessage(new ApiError(404, 'Video not found', 'Video not found'))).toContain('Video not found');
    expect(reselectErrorMessage(new Error('boom'))).toBe('boom');
  });
});

describe('axis caps (Phase F2)', () => {
  it('validates shares and sends only the set axes', async () => {
    const { axisCapErrors, buildAxisCaps } = await import('./reselect.utils');
    const caps = { multi_person: 0.5, solo: null, equipment: 1.4, face: Number.NaN };
    const errors = axisCapErrors(caps);
    expect(errors.equipment).toBeTruthy();
    expect(errors.face).toBeTruthy();
    expect(errors.multi_person).toBeUndefined();
    expect(buildAxisCaps({ multi_person: 0.5, solo: null, equipment: null, face: null })).toEqual({ multi_person: 0.5 });
    expect(buildAxisCaps({ multi_person: null, solo: null, equipment: null, face: null })).toEqual({});
  });

  it('reports cross-dataset blocks and cap overflow in the summary', async () => {
    const { formatReselectSummary } = await import('./reselect.utils');
    const s = formatReselectSummary({
      target_count: 10, picks_count: 10, shortfall: 0, promoted: [1], demoted: [2], locked: 0, dup_skipped: 0,
      excluded_rejected: 0, diversity: 0.5, elasticity: 0.2, learned_weights_active: false, chunks: [],
      axis_caps: { solo: { max_share: 0.3, cap: 3, picks: 4, pool: 9, overflow: 1 } },
      cross_dataset: { enabled: true, reference_frames: 40, blocked: 2 },
    });
    expect(s.message).toContain('2 already in the dataset');
    expect(s.message).toContain('caps exceeded for One person');
  });
});
