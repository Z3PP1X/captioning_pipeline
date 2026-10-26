import { StudioStep } from '../../models/studio.models';

/**
 * Deep links for the 5-step studio: `#/studio/<step>` (e.g. `#/studio/captioning`). Used by the shell to
 * restore the step on reload / back-forward and by the UX gate (Playwright + Lighthouse open views directly).
 */
export const STUDIO_STEPS: readonly StudioStep[] = ['ingest', 'triage', 'tagging', 'captioning', 'export_rag'];

const STUDIO_PREFIX = '#/studio';

export function isStudioHash(hash: string | null | undefined): boolean {
  return !!hash && (hash === STUDIO_PREFIX || hash.startsWith(`${STUDIO_PREFIX}/`));
}

/** `#/studio/triage` -> 'triage'; unknown steps fall back to 'ingest'; non-studio hashes -> null. */
export function parseStudioHash(hash: string | null | undefined): StudioStep | null {
  if (!isStudioHash(hash)) return null;
  const step = (hash ?? '').slice(STUDIO_PREFIX.length).replace(/^\/+/, '').split(/[/?]/)[0].toLowerCase();
  return (STUDIO_STEPS as readonly string[]).includes(step) ? (step as StudioStep) : 'ingest';
}

export function buildStudioHash(step: StudioStep): string {
  return `${STUDIO_PREFIX}/${step}`;
}
