import { describe, it, expect } from 'vitest';
import { ALL_VIDEO_STATUSES, derivedVideoStatus, videoStatusLabel, videoStatusMeta } from './status';

describe('derivedVideoStatus (mirrors reconcile.py)', () => {
  it('is new without chunks or picks', () => {
    expect(derivedVideoStatus({})).toBe('new');
  });

  it('is chunked with saved chunks only (explicit count wins over the list field)', () => {
    expect(derivedVideoStatus({ chunk_count: 3 })).toBe('chunked');
    expect(derivedVideoStatus({ chunk_count: 0 }, 2)).toBe('chunked');
  });

  it('is curating_300 with kept picks that are not all exported', () => {
    expect(derivedVideoStatus({ chunk_count: 4, kept_count: 280, export_count: 100 })).toBe('curating_300');
  });

  it('is exported once every kept pick is exported', () => {
    expect(derivedVideoStatus({ chunk_count: 4, kept_count: 280, export_count: 280 })).toBe('exported');
  });
});

describe('status labels', () => {
  it('labels every status and falls back to the raw value', () => {
    for (const s of ALL_VIDEO_STATUSES) {
      expect(videoStatusLabel(s.value)).toBe(s.label);
      expect(videoStatusMeta({ status: s.value, broken: false }).label).not.toBe('');
    }
    expect(videoStatusLabel('mystery')).toBe('mystery');
    expect(videoStatusLabel(null)).toBe('New');
  });

  it('uses the info variant for curating (in review), warning for running, success for exported', () => {
    expect(videoStatusMeta({ status: 'curating_300', broken: false }).variant).toBe('info');
    expect(videoStatusMeta({ status: 'extracting_750', broken: false }).variant).toBe('warning');
    expect(videoStatusMeta({ status: 'exported', broken: false }).variant).toBe('success');
  });
});
