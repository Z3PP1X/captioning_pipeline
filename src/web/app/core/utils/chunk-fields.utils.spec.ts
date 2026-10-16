import { describe, it, expect } from 'vitest';
import { candidateChunkInfo, chunkTagsText } from './chunk-fields.utils';

describe('chunk field mapper', () => {
  it('reads chunk_pos first, then chunk_position', () => {
    expect(candidateChunkInfo({ chunk_pos: 2, chunk_position: 9 }).position).toBe(2);
    expect(candidateChunkInfo({ chunk_position: 3 }).position).toBe(3);
    expect(candidateChunkInfo({ chunk_pos: 0 }).position).toBe(0);
    expect(candidateChunkInfo({}).position).toBeNull();
  });

  it('maps start/end with 0 fallbacks', () => {
    expect(candidateChunkInfo({ chunk_start: 12.5, chunk_end: 20 })).toMatchObject({ start_s: 12.5, end_s: 20 });
    expect(candidateChunkInfo({})).toMatchObject({ start_s: 0, end_s: 0 });
  });

  it('normalises tags from lists, comma strings and JSON strings', () => {
    expect(chunkTagsText(['rope', 'chair'])).toBe('rope, chair');
    expect(chunkTagsText('rope, chair')).toBe('rope, chair');
    expect(chunkTagsText('["rope","chair"]')).toBe('rope, chair');
    expect(chunkTagsText(undefined)).toBe('');
    expect(candidateChunkInfo({ chunk_tags: ['a'] }).tags).toBe('a');
  });
});
