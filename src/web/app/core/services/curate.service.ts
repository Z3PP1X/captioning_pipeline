import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { CandidateFrame, CandidateState, CurationDistribution } from '../models/candidate.model';
import { ChunkItem } from '../models/chunk.model';
import { ReselectReport, ReselectRequest } from '../models/scoring.model';

/** `POST /api/videos/{id}/reselect`: curation-300 shape plus the report. */
interface ReselectResponse {
  picks?: CandidateFrame[];
  runners?: CandidateFrame[];
  candidates?: CandidateFrame[];
  chunks?: ChunkItem[];
  distribution?: any;
  stats?: any;
  reselect: ReselectReport;
}

@Injectable({
  providedIn: 'root',
})
export class CurateService {
  private readonly api = inject(ApiService);

  readonly candidates = signal<CandidateFrame[]>([]);
  readonly curationChunks = signal<ChunkItem[]>([]);
  readonly distribution = signal<CurationDistribution | null>(null);
  readonly curateChunkFilter = signal<number | 'all'>('all');
  readonly curateRoleFilter = signal<'all' | 'pick' | 'runner_up'>('all');
  readonly curateStateFilter = signal<'all' | 'keep' | 'reject' | 'star'>('all');
  readonly targetPersonOnly = signal<boolean>(false);
  /** Image tag filter: frames carrying the tag (confirmed only, or confirmed + suggested). */
  readonly tagFilter = signal<number | null>(null);
  readonly tagFilterIncludeSuggested = signal<boolean>(true);
  readonly showRunnerUps = signal<boolean>(false);
  readonly lightboxIndex = signal<number | null>(null);
  readonly isLoading = signal<boolean>(false);

  readonly filteredCandidates = computed(() => {
    let list = this.candidates();
    const chunkFilter = this.curateChunkFilter();
    const roleFilter = this.curateRoleFilter();
    const stateFilter = this.curateStateFilter();
    const showRunners = this.showRunnerUps();
    const targetOnly = this.targetPersonOnly();
    const tagFilter = this.tagFilter();
    const tagIncludeSuggested = this.tagFilterIncludeSuggested();

    // Unless explicitly viewing runner ups, show only picks by default if not filtered by role
    if (!showRunners && roleFilter === 'all') {
      list = list.filter((c) => c.is_pick);
    } else if (roleFilter === 'pick') {
      list = list.filter((c) => c.is_pick);
    } else if (roleFilter === 'runner_up') {
      list = list.filter((c) => !c.is_pick);
    }

    if (chunkFilter !== 'all') {
      list = list.filter((c) => String(c.chunk_id) === String(chunkFilter));
    }

    if (stateFilter !== 'all') {
      if (stateFilter === 'keep') {
        list = list.filter((c) => c.state === 'keep' || (c.is_pick && c.state !== 'reject'));
      } else {
        list = list.filter((c) => c.state === stateFilter);
      }
    }

    if (targetOnly) {
      list = list.filter((c) => c.target_person_matched || (c.target_person_sim !== undefined && c.target_person_sim >= 0.363));
    }

    if (tagFilter !== null) {
      list = list.filter((c) =>
        (c.tags || []).some((t) => t.tag_id === tagFilter && (tagIncludeSuggested || t.status === 'confirmed')),
      );
    }

    return list;
  });

  readonly currentLightboxCandidate = computed(() => {
    const idx = this.lightboxIndex();
    const list = this.filteredCandidates();
    if (idx !== null && idx >= 0 && idx < list.length) {
      return list[idx];
    }
    return null;
  });

  async loadCandidates(videoId: string | number): Promise<void> {
    try {
      this.isLoading.set(true);
      const { rawPicks, rawRunners, distData } = await this.fetchCurationData(videoId);

      const normalizedPicks = rawPicks.map((c) => this.normalizeFrame(c, true, videoId));
      const normalizedRunners = rawRunners.map((c) => this.normalizeFrame(c, false, videoId));
      const allCandidates = [...normalizedPicks, ...normalizedRunners];

      this.candidates.set(allCandidates);

      if (distData) {
        this.updateDistributionFromData(distData, allCandidates);
      } else {
        this.recalculateDistribution();
      }
    } catch (err) {
      console.error('Failed to load candidates:', err);
      this.candidates.set([]);
      this.distribution.set(null);
    } finally {
      this.isLoading.set(false);
    }
  }

  /** `curation-300` (updating `curationChunks`), falling back to `/candidates` split by role. */
  private async fetchCurationData(videoId: string | number): Promise<CurationData> {
    try {
      const res = await this.api.get<{
        picks?: CandidateFrame[];
        runners?: CandidateFrame[];
        candidates?: CandidateFrame[];
        chunks?: ChunkItem[];
        distribution?: any;
        stats?: any;
      }>(`/api/videos/${videoId}/curation-300`);

      if (res.chunks && res.chunks.length > 0) {
        this.curationChunks.set(res.chunks);
      }
      return { rawPicks: res.picks || [], rawRunners: res.runners || [], distData: res.distribution || res.stats };
    } catch (err) {
      console.warn('curation-300 fetch failed, trying /candidates fallback:', err);
      const fb = await this.api.get<{
        frames?: CandidateFrame[];
        candidates?: CandidateFrame[];
        distribution?: any;
        stats?: any;
      }>(`/api/videos/${videoId}/candidates`);
      const frames = fb.candidates || fb.frames || [];
      return {
        rawPicks: frames.filter((f) => f.role === 'pick' || f.is_pick),
        rawRunners: frames.filter((f) => f.role !== 'pick' && !f.is_pick),
        distData: fb.distribution || fb.stats,
      };
    }
  }

  /**
   * Phase F reselect: re-runs pick vs runner_up over the stored frames with the given knobs and replaces the
   * candidate list from the curation-300-shaped response. Errors (409 busy, 400 infeasible, 422) are rethrown
   * as ApiError and leave the current list untouched.
   */
  async reselect(videoId: string | number, request: ReselectRequest): Promise<ReselectReport> {
    const res = await this.api.post<ReselectResponse>(`/api/videos/${videoId}/reselect`, request);
    const picks = (res.picks || []).map((c) => this.normalizeFrame(c, true, videoId));
    const runners = (res.runners || []).map((c) => this.normalizeFrame(c, false, videoId));
    const all = [...picks, ...runners];
    this.candidates.set(all);
    if (res.chunks && res.chunks.length > 0) {
      this.curationChunks.set(res.chunks);
    }
    const dist = res.distribution || res.stats;
    if (dist) {
      this.updateDistributionFromData(dist, all);
    } else {
      this.recalculateDistribution();
    }
    return res.reselect;
  }

  /** Unified frame row (curation-300 / candidates / reselect) -> grid candidate. */
  private normalizeFrame(c: CandidateFrame, isPick: boolean, videoId: string | number): CandidateFrame {
    return {
      ...c,
      video_id: String(videoId),
      is_pick: isPick,
      role: isPick ? 'pick' : 'runner_up',
      time_s: c.t !== undefined ? c.t : (c.time_s || 0),
      ...frameUrls(c),
      state: c.state || (isPick ? 'keep' : 'reject'),
      ...framePersonFields(c),
    };
  }

  async setCandidateState(
    videoId: string | number,
    frameId: number,
    state: CandidateState
  ): Promise<void> {
    try {
      await this.api.post(`/api/videos/${videoId}/candidates/${frameId}/state`, { state });
      this.candidates.update((list) =>
        list.map((c) => (c.id === frameId ? { ...c, state } : c))
      );
      this.recalculateDistribution();
    } catch (err) {
      console.error(`Failed to update candidate ${frameId} state:`, err);
      throw err;
    }
  }

  async swapCandidate(videoId: string | number, frameId: number): Promise<void> {
    try {
      const res = await this.api.post<{
        picks?: CandidateFrame[];
        runners?: CandidateFrame[];
        distribution?: any;
        stats?: any;
      }>(`/api/videos/${videoId}/swap-frame`, {
        target_drop_id: frameId,
      });

      if (res.picks && res.runners) {
        const normalizedPicks = res.picks.map((c) => ({
          ...c,
          video_id: String(videoId),
          is_pick: true,
          role: 'pick' as const,
          time_s: c.t !== undefined ? c.t : (c.time_s || 0),
          thumb_url: c.preview_url || `/api/frames/${c.id}/preview`,
          preview_url: c.preview_url || `/api/frames/${c.id}/preview`,
          full_url: c.full_url || `/api/frames/${c.id}/full`,
        }));
        const normalizedRunners = res.runners.map((c) => ({
          ...c,
          video_id: String(videoId),
          is_pick: false,
          role: 'runner_up' as const,
          time_s: c.t !== undefined ? c.t : (c.time_s || 0),
          thumb_url: c.preview_url || `/api/frames/${c.id}/preview`,
          preview_url: c.preview_url || `/api/frames/${c.id}/preview`,
          full_url: c.full_url || `/api/frames/${c.id}/full`,
        }));
        const combined = [...normalizedPicks, ...normalizedRunners];
        this.candidates.set(combined);
        if (res.distribution || res.stats) {
          this.updateDistributionFromData(res.distribution || res.stats, combined);
        } else {
          this.recalculateDistribution();
        }
      } else {
        await this.loadCandidates(videoId);
      }
    } catch (err) {
      console.error(`Failed to swap candidate ${frameId}:`, err);
      throw err;
    }
  }

  async bulkKeepAll(videoId: string | number): Promise<void> {
    try {
      await this.api.post(`/api/videos/${videoId}/curate/keep_all`);
      await this.loadCandidates(videoId);
    } catch (err) {
      console.error('Failed to keep all picks:', err);
      throw err;
    }
  }

  async bulkRejectAll(videoId: string | number): Promise<void> {
    try {
      await this.api.post(`/api/videos/${videoId}/curate/reject_all`);
      await this.loadCandidates(videoId);
    } catch (err) {
      console.error('Failed to reject all candidates:', err);
      throw err;
    }
  }

  async deduplicateSimilarity(
    videoId: string | number,
    similarityThreshold = 0.95,
    targetMin = 150,
    targetMax = 200
  ): Promise<any> {
    try {
      this.isLoading.set(true);
      const res = await this.api.post<any>(`/api/videos/${videoId}/dedup-similarity`, {
        similarity_threshold: similarityThreshold,
        target_min: targetMin,
        target_max: targetMax,
      });
      await this.loadCandidates(videoId);
      return res;
    } catch (err) {
      console.error('Failed to deduplicate similarity:', err);
      throw err;
    } finally {
      this.isLoading.set(false);
    }
  }

  /** Replace the tags of one frame in place (after the user tagged it in the lightbox). */
  setFrameTags(frameId: number, tags: CandidateFrame['tags']): void {
    this.candidates.update((list) => list.map((c) => (c.id === frameId ? { ...c, tags } : c)));
  }

  /** Refresh only the tags of every candidate (after a video-wide re-score), keeping all other state. */
  async refreshTags(videoId: string | number): Promise<void> {
    const res = await this.api.get<{ picks?: CandidateFrame[]; runners?: CandidateFrame[] }>(
      `/api/videos/${videoId}/curation-300`,
    );
    const byId = new Map<number, CandidateFrame['tags']>();
    for (const c of [...(res.picks || []), ...(res.runners || [])]) byId.set(c.id, c.tags || []);
    this.candidates.update((list) => list.map((c) => (byId.has(c.id) ? { ...c, tags: byId.get(c.id) } : c)));
  }

  openLightbox(candidateId: number): void {
    const list = this.filteredCandidates();
    const idx = list.findIndex((c) => c.id === candidateId);
    if (idx !== -1) {
      this.lightboxIndex.set(idx);
    }
  }

  closeLightbox(): void {
    this.lightboxIndex.set(null);
  }

  nextLightbox(): void {
    const idx = this.lightboxIndex();
    const list = this.filteredCandidates();
    if (idx !== null && idx < list.length - 1) {
      this.lightboxIndex.set(idx + 1);
    }
  }

  prevLightbox(): void {
    const idx = this.lightboxIndex();
    if (idx !== null && idx > 0) {
      this.lightboxIndex.set(idx - 1);
    }
  }

  private updateDistributionFromData(
    distData: any,
    allCandidates: CandidateFrame[]
  ): void {
    const selectedCount = allCandidates.filter(isSelected).length;

    this.distribution.set(
      buildDistribution(
        {
          multiple_people: distPct(distData, 'multiple_pct', 'multiple_people'),
          one_person: distPct(distData, 'one_person_pct', 'one_person'),
          tools_equipment: distPct(distData, 'tools_equipment_pct', 'tools_equipment'),
          face_visible: distPct(distData, 'face_visible_pct', 'face_visible'),
          target_person: distPct(distData, 'target_person_pct', 'target_person'),
        },
        selectedCount
      )
    );
  }

  private recalculateDistribution(): void {
    const list = this.candidates();
    const selected = list.filter(isSelected);
    const total = selected.length || 1;
    const pctOf = (match: (c: CandidateFrame) => boolean): number =>
      (selected.filter(match).length / total) * 100;

    this.distribution.set(
      buildDistribution(
        {
          multiple_people: pctOf(isMultiplePeople),
          one_person: pctOf(isOnePerson),
          tools_equipment: pctOf(hasToolsEquipment),
          face_visible: pctOf(isFaceVisible),
          target_person: pctOf(isTargetPerson),
        },
        selected.length
      )
    );
  }
}

interface CurationData {
  rawPicks: CandidateFrame[];
  rawRunners: CandidateFrame[];
  distData: any;
}

type DistributionCategory = 'multiple_people' | 'one_person' | 'tools_equipment' | 'face_visible' | 'target_person';

/** Target share (%) of every distribution category. */
const DISTRIBUTION_TARGETS: Record<DistributionCategory, number> = {
  multiple_people: 50,
  one_person: 25,
  tools_equipment: 20,
  face_visible: 50,
  target_person: 75,
};

function frameUrls(c: CandidateFrame): Pick<CandidateFrame, 'thumb_url' | 'preview_url' | 'full_url'> {
  return {
    thumb_url: c.preview_url || c.thumb_url || `/api/frames/${c.id}/preview`,
    preview_url: c.preview_url || `/api/frames/${c.id}/preview`,
    full_url: c.full_url || `/api/frames/${c.id}/full`,
  };
}

/** Person/face fields, falling back to the zero-shot score bag. */
function framePersonFields(
  c: CandidateFrame
): Pick<CandidateFrame, 'target_person_sim' | 'target_person_matched' | 'face_count' | 'faces'> {
  return { ...frameTargetFields(c), ...frameFaceFields(c) };
}

function frameTargetFields(c: CandidateFrame): Pick<CandidateFrame, 'target_person_sim' | 'target_person_matched'> {
  return {
    target_person_sim: c.target_person_sim ?? c.zs_scores?.['target_person_sim'] ?? 0,
    target_person_matched: Boolean(c.target_person_matched ?? c.zs_scores?.['target_person_matched']),
  };
}

function frameFaceFields(c: CandidateFrame): Pick<CandidateFrame, 'face_count' | 'faces'> {
  return {
    face_count: c.face_count ?? c.zs_scores?.['face_count'] ?? 0,
    faces: c.faces ?? c.zs_scores?.['faces'] ?? [],
  };
}

/** Kept explicitly, or a pick that was not rejected. */
function isSelected(c: CandidateFrame): boolean {
  return c.state === 'keep' || (c.is_pick && c.state !== 'reject');
}

function zsOf(c: CandidateFrame): Record<string, any> {
  return c.zs_scores || c.metrics || {};
}

function isMultiplePeople(c: CandidateFrame): boolean {
  return (zsOf(c)['multiple_persons'] ?? 0) >= 0.45 || !!c.categories?.multiple_people;
}

function isOnePerson(c: CandidateFrame): boolean {
  return (zsOf(c)['one_person'] ?? 0) >= 0.45 || !!c.categories?.one_person;
}

function hasToolsEquipment(c: CandidateFrame): boolean {
  const zs = zsOf(c);
  return Math.max(zs['tools'] ?? 0, zs['equipment'] ?? 0) >= 0.45 || !!c.categories?.tools_equipment;
}

function isFaceVisible(c: CandidateFrame): boolean {
  const zs = zsOf(c);
  return (zs['face_score'] ?? 0) >= 0.50 || !!zs['face_visible_50pct'] || !!c.categories?.face_visible;
}

function isTargetPerson(c: CandidateFrame): boolean {
  return !!c.target_person_matched || (c.target_person_sim ?? 0) >= 0.363;
}

/** `distData.<flatKey>` (e.g. `multiple_pct`), else `distData.<nestedKey>.pct`, else 0. */
function distPct(distData: any, flatKey: string, nestedKey: DistributionCategory): number {
  return distData?.[flatKey] ?? distData?.[nestedKey]?.pct ?? 0;
}

/** Builds the curation distribution from raw (unrounded) category percentages. */
function buildDistribution(pcts: Record<DistributionCategory, number>, selectedCount: number): CurationDistribution {
  const entry = (key: DistributionCategory) => ({ pct: Math.round(pcts[key]), target_pct: DISTRIBUTION_TARGETS[key] });
  return {
    multiple_people: entry('multiple_people'),
    one_person: entry('one_person'),
    tools_equipment: entry('tools_equipment'),
    face_visible: entry('face_visible'),
    target_person: entry('target_person'),
    target_person_pct: Math.round(pcts.target_person),
    selected_count: selectedCount,
    target_count: 300,
  };
}
