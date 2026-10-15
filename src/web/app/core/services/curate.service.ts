import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { CandidateFrame, CandidateState, CurationDistribution } from '../models/candidate.model';
import { ChunkItem } from '../models/chunk.model';

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
      let rawPicks: CandidateFrame[] = [];
      let rawRunners: CandidateFrame[] = [];
      let distData: any = null;

      try {
        const res = await this.api.get<{
          picks?: CandidateFrame[];
          runners?: CandidateFrame[];
          candidates?: CandidateFrame[];
          chunks?: ChunkItem[];
          distribution?: any;
          stats?: any;
        }>(`/api/videos/${videoId}/curation-300`);

        rawPicks = res.picks || [];
        rawRunners = res.runners || [];
        distData = res.distribution || res.stats;
        if (res.chunks && res.chunks.length > 0) {
          this.curationChunks.set(res.chunks);
        }
      } catch (err) {
        console.warn('curation-300 fetch failed, trying /candidates fallback:', err);
        const fb = await this.api.get<{
          frames?: CandidateFrame[];
          candidates?: CandidateFrame[];
          distribution?: any;
          stats?: any;
        }>(`/api/videos/${videoId}/candidates`);
        const frames = fb.candidates || fb.frames || [];
        rawPicks = frames.filter((f) => f.role === 'pick' || f.is_pick);
        rawRunners = frames.filter((f) => f.role !== 'pick' && !f.is_pick);
        distData = fb.distribution || fb.stats;
      }

      const mapCandidate = (c: CandidateFrame, isPick: boolean): CandidateFrame => ({
        ...c,
        video_id: String(videoId),
        is_pick: isPick,
        role: isPick ? 'pick' : 'runner_up',
        time_s: c.t !== undefined ? c.t : (c.time_s || 0),
        thumb_url: c.preview_url || c.thumb_url || `/api/frames/${c.id}/preview`,
        preview_url: c.preview_url || `/api/frames/${c.id}/preview`,
        full_url: c.full_url || `/api/frames/${c.id}/full`,
        state: c.state || (isPick ? 'keep' : 'reject'),
        target_person_sim: c.target_person_sim ?? c.zs_scores?.['target_person_sim'] ?? 0,
        target_person_matched: Boolean(c.target_person_matched ?? c.zs_scores?.['target_person_matched']),
        face_count: c.face_count ?? c.zs_scores?.['face_count'] ?? 0,
        faces: c.faces ?? c.zs_scores?.['faces'] ?? [],
      });

      const normalizedPicks = rawPicks.map((c) => mapCandidate(c, true));
      const normalizedRunners = rawRunners.map((c) => mapCandidate(c, false));
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
    const multPct = distData?.multiple_pct ?? distData?.multiple_people?.pct ?? 0;
    const soloPct = distData?.one_person_pct ?? distData?.one_person?.pct ?? 0;
    const toolsPct = distData?.tools_equipment_pct ?? distData?.tools_equipment?.pct ?? 0;
    const facePct = distData?.face_visible_pct ?? distData?.face_visible?.pct ?? 0;
    const targetPct = distData?.target_person_pct ?? distData?.target_person?.pct ?? 0;

    const selectedCount = allCandidates.filter((c) => c.state === 'keep' || (c.is_pick && c.state !== 'reject')).length;

    this.distribution.set({
      multiple_people: {
        pct: Math.round(multPct),
        target_pct: 50,
      },
      one_person: {
        pct: Math.round(soloPct),
        target_pct: 25,
      },
      tools_equipment: {
        pct: Math.round(toolsPct),
        target_pct: 20,
      },
      face_visible: {
        pct: Math.round(facePct),
        target_pct: 50,
      },
      target_person: {
        pct: Math.round(targetPct),
        target_pct: 75,
      },
      target_person_pct: Math.round(targetPct),
      selected_count: selectedCount,
      target_count: 300,
    });
  }

  private recalculateDistribution(): void {
    const list = this.candidates();
    const selected = list.filter((c) => c.state === 'keep' || (c.is_pick && c.state !== 'reject'));
    const total = selected.length || 1;

    let multCount = 0;
    let soloCount = 0;
    let toolsCount = 0;
    let faceCount = 0;
    let targetCount = 0;

    for (const c of selected) {
      const zs = c.zs_scores || c.metrics || {};
      if ((zs['multiple_persons'] ?? 0) >= 0.45 || c.categories?.multiple_people) multCount++;
      if ((zs['one_person'] ?? 0) >= 0.45 || c.categories?.one_person) soloCount++;
      if (Math.max(zs['tools'] ?? 0, zs['equipment'] ?? 0) >= 0.45 || c.categories?.tools_equipment) toolsCount++;
      if ((zs['face_score'] ?? 0) >= 0.50 || zs['face_visible_50pct'] || c.categories?.face_visible) faceCount++;
      if (c.target_person_matched || (c.target_person_sim ?? 0) >= 0.363) targetCount++;
    }

    this.distribution.set({
      multiple_people: {
        pct: Math.round((multCount / total) * 100),
        target_pct: 50,
      },
      one_person: {
        pct: Math.round((soloCount / total) * 100),
        target_pct: 25,
      },
      tools_equipment: {
        pct: Math.round((toolsCount / total) * 100),
        target_pct: 20,
      },
      face_visible: {
        pct: Math.round((faceCount / total) * 100),
        target_pct: 50,
      },
      target_person: {
        pct: Math.round((targetCount / total) * 100),
        target_pct: 75,
      },
      target_person_pct: Math.round((targetCount / total) * 100),
      selected_count: selected.length,
      target_count: 300,
    });
  }
}
