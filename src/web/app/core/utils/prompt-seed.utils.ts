import { CaptionRow } from '../models/caption.model';
import { ApiError } from '../services/api-error';

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Prompt Director description from a frame caption: the caption text without its leading trigger token
 * (the Director adds the configured trigger itself), whitespace collapsed. Null when there is no usable text.
 */
export function captionToSeedDescription(caption: Pick<CaptionRow, 'text' | 'trigger'> | null | undefined): string | null {
  let text = (caption?.text ?? '').replace(/\s+/g, ' ').trim();
  const trigger = (caption?.trigger ?? '').trim();
  if (text && trigger) {
    text = text.replace(new RegExp(`^${escapeRegExp(trigger)}(?:[\\s,.;:]+|$)`, 'i'), '').trim();
  }
  return text || null;
}

/** Readable message for a failed `GET /api/rag/similar`; null for 409 (= image index not built, not an error). */
export function similarErrorMessage(err: unknown, frameId: number): string | null {
  if (err instanceof ApiError) {
    if (err.status === 409) return null;
    if (err.status === 404) {
      return /unknown api route/i.test(err.message)
        ? 'Similar-frame search is not available on this server yet. Restart the backend once it serves /api/rag/similar.'
        : `Frame #${frameId} no longer exists (it may have been re-extracted).`;
    }
    return err.message || `Similar-frame search failed (HTTP ${err.status}).`;
  }
  return err instanceof Error ? err.message : String(err);
}
