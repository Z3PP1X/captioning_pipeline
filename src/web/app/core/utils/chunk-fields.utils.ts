import { CandidateFrame } from '../models/candidate.model';

/** The chunk a candidate row belongs to, independent of which endpoint produced the row. */
export interface CandidateChunkInfo {
  /** 0-based chunk position, or null when the row carries none. */
  position: number | null;
  start_s: number;
  end_s: number;
  tags: string;
}

function finiteOr<T>(value: unknown, fallback: T): number | T {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof n === 'number' && Number.isFinite(n) ? n : fallback;
}

/** Chunk tags come as a list (chunk rows) or as a comma string / JSON-ish string (frame rows). */
export function chunkTagsText(tags: string[] | string | null | undefined): string {
  if (Array.isArray(tags)) return tags.filter(Boolean).join(', ');
  if (typeof tags !== 'string') return '';
  const text = tags.trim();
  if (text.startsWith('[')) {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed.filter(Boolean).join(', ');
    } catch {
      /* not JSON: use as is */
    }
  }
  return text;
}

/**
 * Single mapper for the per-endpoint chunk field aliases on frame rows (`chunk_pos` / `chunk_position`,
 * `chunk_start`, `chunk_end`, `chunk_tags`; see api-contract "Frame row").
 */
export function candidateChunkInfo(c: Partial<CandidateFrame>): CandidateChunkInfo {
  const position = finiteOr(c.chunk_pos, null) ?? finiteOr(c.chunk_position, null);
  return {
    position,
    start_s: finiteOr(c.chunk_start, 0),
    end_s: finiteOr(c.chunk_end, 0),
    tags: chunkTagsText(c.chunk_tags),
  };
}
