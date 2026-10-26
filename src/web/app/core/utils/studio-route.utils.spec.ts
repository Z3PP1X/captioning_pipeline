import { buildStudioHash, isStudioHash, parseStudioHash } from './studio-route.utils';

describe('studio-route utils', () => {
  it('recognises studio hashes only', () => {
    expect(isStudioHash('#/studio/triage')).toBe(true);
    expect(isStudioHash('#/studio')).toBe(true);
    expect(isStudioHash('#/curate?video=1')).toBe(false);
    expect(isStudioHash('')).toBe(false);
    expect(isStudioHash(null)).toBe(false);
  });

  it('parses the step and falls back to ingest', () => {
    expect(parseStudioHash('#/studio/captioning')).toBe('captioning');
    expect(parseStudioHash('#/studio/EXPORT_RAG')).toBe('export_rag');
    expect(parseStudioHash('#/studio/unknown')).toBe('ingest');
    expect(parseStudioHash('#/studio')).toBe('ingest');
    expect(parseStudioHash('#/board')).toBeNull();
  });

  it('round-trips', () => {
    expect(parseStudioHash(buildStudioHash('tagging'))).toBe('tagging');
  });
});
