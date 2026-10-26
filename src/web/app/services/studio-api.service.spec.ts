import { describe, expect, it } from 'vitest';
import { computeCaptionWordDiff } from './studio-api.service';

describe('computeCaptionWordDiff', () => {
  it('returns empty array for two empty captions', () => {
    expect(computeCaptionWordDiff('', '')).toEqual([]);
  });

  it('identifies unchanged, added, and removed tokens accurately', () => {
    const before = 'ohwx_woman Photograph of an adult woman in dark coat';
    const after = 'ohwx_woman Photograph of an athletic adult woman in tailored coat';
    const tokens = computeCaptionWordDiff(before, after);

    const added = tokens.filter((t) => t.type === 'added').map((t) => t.text);
    const removed = tokens.filter((t) => t.type === 'removed').map((t) => t.text);
    expect(added).toContain('athletic');
    expect(added).toContain('tailored');
    expect(removed).toContain('dark');
  });
});
