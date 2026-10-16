/**
 * Hash-based deep links (no Angular Router): `#/<tab>` or `#/<tab>?video=<id>`, e.g. `#/curate?video=abc123`.
 */
export type AppTab = 'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker';

export const APP_TABS: readonly AppTab[] = ['board', 'studio', 'curate', 'dataset', 'prompt-clicker'];
export const DEFAULT_TAB: AppTab = 'board';

export interface HashRoute {
  tab: AppTab;
  videoId: string | null;
}

export function isAppTab(value: string): value is AppTab {
  return (APP_TABS as readonly string[]).includes(value);
}

/** Route hashes start with `#/`; anything else (e.g. an in-page anchor like `#main-content`) is not a route. */
export function isRouteHash(hash: string | null | undefined): boolean {
  return !hash || hash === '#' || hash.startsWith('#/');
}

/** Parses `location.hash`; unknown tabs fall back to the board, a missing/empty `video` to null. */
export function parseHashRoute(hash: string | null | undefined): HashRoute {
  const raw = (hash ?? '').replace(/^#/, '').replace(/^\//, '');
  const qIdx = raw.indexOf('?');
  const path = (qIdx >= 0 ? raw.slice(0, qIdx) : raw).replace(/\/+$/, '').toLowerCase();
  const query = new URLSearchParams(qIdx >= 0 ? raw.slice(qIdx + 1) : '');
  const video = query.get('video')?.trim() || null;
  return {
    tab: isAppTab(path) ? path : DEFAULT_TAB,
    videoId: video,
  };
}

/** Builds the hash for a tab + optional video (the Prompt Director is independent of the video). */
export function buildHashRoute(tab: AppTab, videoId: string | number | null | undefined): string {
  const id = videoId === null || videoId === undefined ? '' : String(videoId);
  if (!id || tab === 'prompt-clicker') return `#/${tab}`;
  return `#/${tab}?video=${encodeURIComponent(id)}`;
}
