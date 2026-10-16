import { describe, it, expect } from 'vitest';
import { captionToSeedDescription, similarErrorMessage } from './prompt-seed.utils';
import { ApiError } from '../services/api-error';

describe('captionToSeedDescription', () => {
  it('strips the leading trigger token and collapses whitespace', () => {
    expect(captionToSeedDescription({ text: 'restrained_elegance, a woman   in a red\n dress.', trigger: 'restrained_elegance' })).toBe(
      'a woman in a red dress.',
    );
    expect(captionToSeedDescription({ text: 'Tok: kneeling on a mat', trigger: 'tok' })).toBe('kneeling on a mat');
  });

  it('keeps the text when it does not start with the trigger (or the trigger is only a prefix of a word)', () => {
    expect(captionToSeedDescription({ text: 'a woman, restrained_elegance', trigger: 'restrained_elegance' })).toBe(
      'a woman, restrained_elegance',
    );
    expect(captionToSeedDescription({ text: 'token ring', trigger: 'tok' })).toBe('token ring');
    expect(captionToSeedDescription({ text: 'plain caption', trigger: null })).toBe('plain caption');
  });

  it('returns null without usable text', () => {
    expect(captionToSeedDescription(null)).toBeNull();
    expect(captionToSeedDescription({ text: '   ', trigger: null })).toBeNull();
    expect(captionToSeedDescription({ text: 'tok', trigger: 'tok' })).toBeNull();
  });
});

describe('similarErrorMessage', () => {
  it('treats 409 as "index not ready" (no error text)', () => {
    expect(similarErrorMessage(new ApiError(409, 'no image index', 'no image index'), 3)).toBeNull();
  });

  it('distinguishes a missing route from an unknown frame', () => {
    expect(similarErrorMessage(new ApiError(404, 'x', 'Unknown API route: /api/rag/similar'), 3)).toContain('not available');
    expect(similarErrorMessage(new ApiError(404, 'Frame not found', 'Frame not found'), 3)).toContain('Frame #3');
    expect(similarErrorMessage(new ApiError(500, 'oops', 'oops'), 3)).toBe('oops');
  });
});
