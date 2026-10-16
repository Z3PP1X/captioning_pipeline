import { describe, it, expect } from 'vitest';
import { buildHashRoute, isRouteHash, parseHashRoute } from './hash-route.utils';

describe('hash-route utils', () => {
  describe('parseHashRoute', () => {
    it('parses tab and video id', () => {
      expect(parseHashRoute('#/curate?video=abc123')).toEqual({ tab: 'curate', videoId: 'abc123' });
    });

    it('parses a tab without video', () => {
      expect(parseHashRoute('#/dataset')).toEqual({ tab: 'dataset', videoId: null });
      expect(parseHashRoute('#/prompt-clicker')).toEqual({ tab: 'prompt-clicker', videoId: null });
    });

    it('tolerates a missing leading slash, trailing slash and casing', () => {
      expect(parseHashRoute('#studio')).toEqual({ tab: 'studio', videoId: null });
      expect(parseHashRoute('#/Studio/?video=v1')).toEqual({ tab: 'studio', videoId: 'v1' });
    });

    it('falls back to the board for empty or unknown hashes', () => {
      expect(parseHashRoute('')).toEqual({ tab: 'board', videoId: null });
      expect(parseHashRoute(null)).toEqual({ tab: 'board', videoId: null });
      expect(parseHashRoute('#/nope?video=x')).toEqual({ tab: 'board', videoId: 'x' });
    });

    it('treats an empty video parameter as no video and decodes encoded ids', () => {
      expect(parseHashRoute('#/curate?video=')).toEqual({ tab: 'curate', videoId: null });
      expect(parseHashRoute('#/curate?video=a%20b%2Fc')).toEqual({ tab: 'curate', videoId: 'a b/c' });
    });
  });

  describe('isRouteHash', () => {
    it('accepts route hashes and the empty hash, rejects in-page anchors', () => {
      expect(isRouteHash('')).toBe(true);
      expect(isRouteHash('#')).toBe(true);
      expect(isRouteHash('#/curate?video=1')).toBe(true);
      expect(isRouteHash('#main-content')).toBe(false);
    });
  });

  describe('buildHashRoute', () => {
    it('builds tab + video hashes and round-trips through the parser', () => {
      const hash = buildHashRoute('curate', 'a b/c');
      expect(hash).toBe('#/curate?video=a%20b%2Fc');
      expect(parseHashRoute(hash)).toEqual({ tab: 'curate', videoId: 'a b/c' });
    });

    it('omits the video when there is none or on the Prompt Director', () => {
      expect(buildHashRoute('studio', null)).toBe('#/studio');
      expect(buildHashRoute('prompt-clicker', 'v1')).toBe('#/prompt-clicker');
      expect(buildHashRoute('board', 42)).toBe('#/board?video=42');
    });
  });
});
