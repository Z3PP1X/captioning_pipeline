import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import {
  CurationState,
  DatasetExportResponse,
  DiffToken,
  ExtractionConfigDto,
  ExtractionMode,
  FsBrowseResponse,
  HealthStatus,
  OpenRouterTestResponse,
  RagGenerateResponse,
  RagIndexResponse,
  RagRetrievedExemplar,
  RejectionLogEntry,
  RejectionsResponse,
  SourceType,
  StratificationReport,
  StratifyResult,
  StudioDataset,
  StudioMediaItem,
  StudioSkill,
  StudioStep,
  StudioTelemetryEvent,
  SystemSettingsMap,
  TriggerPosition,
} from '../models/studio.models';

function buildLcsTable(a: string[], b: string[]): number[][] {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? 1 + dp[i + 1][j + 1] : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

/**
 * Compute word-level LCS diff tokens between two caption strings for the Step 4 Live Diff View.
 */
export function computeCaptionWordDiff(beforeText: string, afterText: string): DiffToken[] {
  const a = (beforeText || '').trim().split(/\s+/).filter(Boolean);
  const b = (afterText || '').trim().split(/\s+/).filter(Boolean);
  if (a.length === 0 && b.length === 0) {
    return [];
  }
  const dp = buildLcsTable(a, b);
  const out: DiffToken[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: a[i++] });
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: 'removed', text: a[i++] });
    } else {
      out.push({ type: 'added', text: b[j++] });
    }
  }
  for (; i < a.length; i++) out.push({ type: 'removed', text: a[i] });
  for (; j < b.length; j++) out.push({ type: 'added', text: b[j] });
  return out;
}

@Injectable({
  providedIn: 'root',
})
export class StudioApiService {
  private readonly http = inject(HttpClient);
  private eventSource: EventSource | null = null;
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  // =========================================================================
  // Core Reactive Signals
  // =========================================================================
  readonly activeStep = signal<StudioStep>('ingest');
  readonly health = signal<HealthStatus | null>(null);
  readonly settings = signal<SystemSettingsMap>({
    primary_model: 'google/gemma-4-31b-it',
    fallback_model_1: 'deepseek/deepseek-v4.1-flash',
    fallback_model_2: 'x-ai/grok-4.7',
    max_tokens: 600,
    temperature: 0.4,
    trigger_word: 'ohwx_subject',
    trigger_position: 'prefix',
  });

  readonly datasets = signal<StudioDataset[]>([]);
  readonly activeDatasetId = signal<string | null>(null);
  readonly activeDataset = computed<StudioDataset | null>(() => {
    const id = this.activeDatasetId();
    const list = this.datasets();
    if (!id) {
      return list[0] ?? null;
    }
    return list.find((d) => d.id === id || d.slug === id) ?? list[0] ?? null;
  });

  readonly mediaItems = signal<StudioMediaItem[]>([]);
  readonly totalMediaItems = signal<number>(0);
  readonly loadingItems = signal<boolean>(false);

  readonly skills = signal<StudioSkill[]>([]);
  readonly stratification = signal<StratificationReport | null>(null);
  readonly rejectionLogs = signal<RejectionLogEntry[]>([]);
  readonly needsManualCount = signal<number>(0);
  readonly lastExportResult = signal<DatasetExportResponse | null>(null);

  // =========================================================================
  // Live SSE / WebSocket Telemetry Signals
  // =========================================================================
  readonly sseState = signal<'connected' | 'connecting' | 'disconnected'>('disconnected');
  readonly activeJob = signal<{
    job_id: number;
    dataset_id: string;
    progress: number;
    step: string;
    cost_usd: number;
  } | null>(null);

  readonly telemetryLog = signal<StudioTelemetryEvent[]>([]);
  readonly cascadeStats = signal<{
    acceptedPrimary: number;
    acceptedFallback1: number;
    acceptedFallback2: number;
    rejectedCount: number;
  }>({
    acceptedPrimary: 0,
    acceptedFallback1: 0,
    acceptedFallback2: 0,
    rejectedCount: 0,
  });

  readonly toastMessage = signal<{
    text: string;
    tone: 'info' | 'success' | 'warning' | 'error';
  } | null>(null);

  showToast(text: string, tone: 'info' | 'success' | 'warning' | 'error' = 'info'): void {
    this.toastMessage.set({ text, tone });
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
    }
    this.toastTimer = setTimeout(() => {
      this.toastMessage.set(null);
    }, 4200);
  }

  // =========================================================================
  // Real-Time SSE Connection (/api/v1/events/stream)
  // =========================================================================
  connectTelemetryStream(): void {
    if (typeof EventSource === 'undefined' || this.eventSource) {
      return;
    }
    this.sseState.set('connecting');
    try {
      const es = new EventSource('/api/v1/events/stream');
      this.eventSource = es;

      es.onopen = () => {
        this.sseState.set('connected');
      };

      es.onmessage = (ev: MessageEvent) => {
        if (!ev.data) return;
        try {
          const parsed = JSON.parse(String(ev.data)) as StudioTelemetryEvent;
          this.handleTelemetryEvent(parsed);
        } catch {
          // Ignore non-JSON heartbeat lines
        }
      };

      const namedEvents = [
        'job_started',
        'job_progress',
        'job_finished',
        'job_error',
        'job_cancelled',
        'caption_accepted',
        'caption_rejected',
      ];
      for (const evType of namedEvents) {
        es.addEventListener(evType, (ev: Event) => {
          const msgEv = ev as MessageEvent;
          if (!msgEv.data) return;
          try {
            const parsed = JSON.parse(String(msgEv.data)) as StudioTelemetryEvent;
            this.handleTelemetryEvent(parsed);
          } catch {
            // Ignore malformed event
          }
        });
      }

      es.onerror = () => {
        this.sseState.set('disconnected');
        es.close();
        this.eventSource = null;
      };
    } catch {
      this.sseState.set('disconnected');
    }
  }

  private handleTelemetryEvent(ev: StudioTelemetryEvent): void {
    this.telemetryLog.update((prev) => [ev, ...prev.slice(0, 49)]);
    const d = ev.data || {};

    if (ev.type === 'job_started' || ev.type === 'job_progress') {
      this.handleJobProgressTelemetry(d);
    } else if (ev.type === 'job_finished') {
      this.handleJobFinishedTelemetry(d);
    } else if (ev.type === 'caption_accepted') {
      this.handleCaptionAcceptedTelemetry(d.layer ?? 'primary');
    } else if (ev.type === 'caption_rejected') {
      this.cascadeStats.update((s) => ({ ...s, rejectedCount: s.rejectedCount + 1 }));
    }
  }

  private handleJobProgressTelemetry(d: StudioTelemetryEvent['data']): void {
    this.activeJob.set({
      job_id: d.job_id ?? 1,
      dataset_id: d.dataset_id ?? this.activeDatasetId() ?? '',
      progress: typeof d.progress === 'number' ? d.progress : 0.1,
      step: d.step ?? 'Processing...',
      cost_usd: d.cost_usd ?? 0,
    });
  }

  private handleJobFinishedTelemetry(d: StudioTelemetryEvent['data']): void {
    this.activeJob.set({
      job_id: d.job_id ?? 1,
      dataset_id: d.dataset_id ?? this.activeDatasetId() ?? '',
      progress: 1.0,
      step: d.step ?? 'Completed',
      cost_usd: d.cost_usd ?? 0,
    });
    this.showToast(d.step ?? 'Background job completed', 'success');
    void this.refreshDatasets();
    const activeId = this.activeDataset()?.id;
    if (activeId) {
      void this.loadMediaItems(activeId);
    }
    setTimeout(() => {
      const cur = this.activeJob();
      if (cur && cur.progress >= 1.0) {
        this.activeJob.set(null);
      }
    }, 3000);
  }

  private handleCaptionAcceptedTelemetry(layer: string): void {
    this.cascadeStats.update((s) => ({
      ...s,
      acceptedPrimary: layer === 'primary' ? s.acceptedPrimary + 1 : s.acceptedPrimary,
      acceptedFallback1: layer === 'fallback_1' ? s.acceptedFallback1 + 1 : s.acceptedFallback1,
      acceptedFallback2: layer === 'fallback_2' ? s.acceptedFallback2 + 1 : s.acceptedFallback2,
    }));
  }

  // =========================================================================
  // 5.1 System & Configuration Endpoints
  // =========================================================================
  async loadHealth(): Promise<HealthStatus | null> {
    try {
      const res = await firstValueFrom(this.http.get<HealthStatus>('/api/v1/health'));
      this.health.set(res);
      return res;
    } catch {
      return null;
    }
  }

  async loadSettings(): Promise<SystemSettingsMap> {
    try {
      const res = await firstValueFrom(
        this.http.get<{ settings: SystemSettingsMap }>('/api/v1/settings'),
      );
      if (res?.settings) {
        this.settings.set(res.settings);
      }
      return this.settings();
    } catch {
      return this.settings();
    }
  }

  async updateSettings(payload: Partial<SystemSettingsMap> & { openrouter_api_key?: string }): Promise<SystemSettingsMap> {
    const res = await firstValueFrom(
      this.http.put<{ settings: SystemSettingsMap; ignored_keys: string[] }>(
        '/api/v1/settings',
        payload,
      ),
    );
    if (res?.settings) {
      this.settings.set(res.settings);
    }
    this.showToast('System settings saved to SQLite WAL', 'success');
    return this.settings();
  }

  async testOpenRouterConnection(apiKeyOverride?: string): Promise<OpenRouterTestResponse> {
    return await firstValueFrom(
      this.http.post<OpenRouterTestResponse>('/api/v1/settings/test-openrouter', {
        api_key: apiKeyOverride || undefined,
      }),
    );
  }

  // =========================================================================
  // 5.2 Step 1: Ingestion, Extraction & Deduplication
  // =========================================================================
  async browseFilesystem(subpath = ''): Promise<FsBrowseResponse> {
    const params = new HttpParams().set('subpath', subpath);
    return await firstValueFrom(
      this.http.get<FsBrowseResponse>('/api/v1/fs/browse', { params }),
    );
  }

  async refreshDatasets(status?: string, search?: string): Promise<StudioDataset[]> {
    try {
      let params = new HttpParams();
      if (status) params = params.set('status', status);
      if (search) params = params.set('search', search);
      const res = await firstValueFrom(
        this.http.get<{ datasets: StudioDataset[] }>('/api/v1/datasets', { params }),
      );
      const list = res?.datasets ?? [];
      this.datasets.set(list);
      if (!this.activeDatasetId() && list.length > 0) {
        this.activeDatasetId.set(list[0].id);
      }
      return list;
    } catch {
      return this.datasets();
    }
  }

  async createDataset(payload: {
    name: string;
    source_type: SourceType;
    source_path: string;
    trigger_word: string;
    trigger_position: TriggerPosition;
    active_skill_id?: string | null;
    target_count: number;
    extraction_config: ExtractionConfigDto;
    auto_ingest?: boolean;
  }): Promise<{ dataset: StudioDataset; job_id: number; status: string }> {
    const res = await firstValueFrom(
      this.http.post<{ dataset: StudioDataset; job_id: number; status: string }>(
        '/api/v1/datasets',
        payload,
      ),
    );
    await this.refreshDatasets();
    if (res?.dataset?.id) {
      this.activeDatasetId.set(res.dataset.id);
      await this.loadMediaItems(res.dataset.id);
    }
    this.showToast(`Dataset "${payload.name}" created & ingested`, 'success');
    return res;
  }

  async reIngestDataset(
    datasetId: string,
    payload: {
      mode: ExtractionMode;
      scene_threshold: number;
      interval_s: number;
      window_peak: number;
      target_candidates: number;
      dedup_hamming_threshold: number;
    },
  ): Promise<{ job_id: number; status: string; dataset?: StudioDataset }> {
    const res = await firstValueFrom(
      this.http.post<{ job_id: number; status: string; dataset?: StudioDataset }>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/ingest`,
        payload,
      ),
    );
    await this.refreshDatasets();
    await this.loadMediaItems(datasetId);
    this.showToast('Re-ingestion & mathematical CV scoring complete', 'success');
    return res;
  }

  async reclusterDataset(
    datasetId: string,
    payload: {
      hamming_threshold: number;
      dhash_threshold?: number;
      hsv_min_cosine?: number;
      auto_select_representatives?: boolean;
    },
  ): Promise<{
    clusters_count: number;
    duplicates_collapsed: number;
    representatives_kept: number;
  }> {
    const res = await firstValueFrom(
      this.http.post<{
        clusters_count: number;
        duplicates_collapsed: number;
        representatives_kept: number;
      }>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/cluster`, {
        hamming_threshold: payload.hamming_threshold,
        dhash_threshold: payload.dhash_threshold ?? 8,
        hsv_min_cosine: payload.hsv_min_cosine ?? 0.85,
        auto_select_representatives: payload.auto_select_representatives ?? true,
      }),
    );
    await this.refreshDatasets();
    await this.loadMediaItems(datasetId);
    this.showToast(
      `Clustered into ${res.clusters_count} groups (${res.duplicates_collapsed} near-duplicates collapsed)`,
      'success',
    );
    return res;
  }

  // =========================================================================
  // 5.3 Step 2 & Step 3: HITL Curation, Triage, Tagging & Stratification
  // =========================================================================
  async loadMediaItems(
    datasetId: string,
    filters?: {
      curation_state?: string;
      cluster_id?: number;
      min_quality?: number;
      tag_slug?: string;
      caption_status?: string;
      include_duplicates?: boolean;
      limit?: number;
      offset?: number;
    },
  ): Promise<{ items: StudioMediaItem[]; total: number }> {
    this.loadingItems.set(true);
    try {
      const params = this.buildMediaItemsParams(filters);
      const res = await firstValueFrom(
        this.http.get<{ items: StudioMediaItem[]; total: number }>(
          `/api/v1/datasets/${encodeURIComponent(datasetId)}/items`,
          { params },
        ),
      );
      return this.applyLoadedMediaItems(res, filters?.cluster_id === undefined);
    } finally {
      this.loadingItems.set(false);
    }
  }

  private buildMediaItemsParams(filters?: {
    curation_state?: string;
    cluster_id?: number;
    min_quality?: number;
    tag_slug?: string;
    caption_status?: string;
    include_duplicates?: boolean;
    limit?: number;
    offset?: number;
  }): HttpParams {
    const f = filters ?? {};
    let params = new HttpParams()
      .set('include_duplicates', String(f.include_duplicates ?? true))
      .set('limit', String(f.limit ?? 500))
      .set('offset', String(f.offset ?? 0));

    const optionalPairs: Array<[string, string | number | undefined]> = [
      ['curation_state', f.curation_state],
      ['cluster_id', f.cluster_id],
      ['min_quality', f.min_quality && f.min_quality > 0 ? f.min_quality : undefined],
      ['tag_slug', f.tag_slug],
      ['caption_status', f.caption_status],
    ];
    for (const [key, val] of optionalPairs) {
      if (val !== undefined && val !== null && val !== '') {
        params = params.set(key, String(val));
      }
    }
    return params;
  }

  private applyLoadedMediaItems(
    res: { items?: StudioMediaItem[]; total?: number } | null | undefined,
    updateStore: boolean,
  ): { items: StudioMediaItem[]; total: number } {
    const items = res?.items ?? [];
    const total = res?.total ?? items.length;
    if (updateStore) {
      this.mediaItems.set(items);
      this.totalMediaItems.set(total);
    }
    return { items, total };
  }

  async batchTriage(
    datasetId: string,
    itemIds: number[],
    curationState: CurationState,
    cropGeometry?: { x: number; y: number; width: number; height: number },
  ): Promise<{ updated: number; curation_state: CurationState }> {
    // Optimistically update local signal for <16ms zero-friction keyboard response
    const idSet = new Set(itemIds);
    this.mediaItems.update((list) =>
      list.map((it) =>
        idSet.has(it.id)
          ? {
              ...it,
              curation_state: curationState,
              crop_geometry: cropGeometry ?? it.crop_geometry,
            }
          : it,
      ),
    );

    const res = await firstValueFrom(
      this.http.post<{ updated: number; curation_state: CurationState }>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/items/batch-triage`,
        {
          item_ids: itemIds,
          curation_state: curationState,
          crop_geometry: cropGeometry,
        },
      ),
    );
    void this.refreshDatasets();
    return res;
  }

  async swapClusterRepresentative(
    datasetId: string,
    clusterId: number,
    newRepresentativeId: number,
  ): Promise<{
    cluster_id: number;
    representative_media_id: number;
    demoted_media_id: number | null;
  }> {
    const res = await firstValueFrom(
      this.http.post<{
        cluster_id: number;
        representative_media_id: number;
        demoted_media_id: number | null;
      }>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/clusters/${clusterId}/representative`,
        { new_representative_id: newRepresentativeId },
      ),
    );
    await this.loadMediaItems(datasetId);
    await this.refreshDatasets();
    this.showToast(`Promoted item #${newRepresentativeId} to cluster #${clusterId} representative`, 'success');
    return res;
  }

  async loadStratification(datasetId: string): Promise<StratificationReport> {
    const res = await firstValueFrom(
      this.http.get<StratificationReport>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/stratification`,
      ),
    );
    this.stratification.set(res);
    return res;
  }

  async applyStratification(
    datasetId: string,
    targetCount: number,
    diversityWeight: number,
  ): Promise<StratifyResult> {
    const res = await firstValueFrom(
      this.http.post<StratifyResult>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/stratify`,
        {
          target_count: targetCount,
          diversity_weight: diversityWeight,
        },
      ),
    );
    await this.loadMediaItems(datasetId);
    await this.loadStratification(datasetId);
    await this.refreshDatasets();
    this.showToast(
      `Auto-stratified dataset to ${res.kept_count} balanced items (${res.outlier_ids.length} outliers flagged)`,
      'success',
    );
    return res;
  }

  async runAutotag(
    datasetId: string,
    pluginId = 'rule_cv_heuristics',
    minConfidence = 0.6,
  ): Promise<{ items_tagged: number; tags_assigned: number; plugin_id: string }> {
    const res = await firstValueFrom(
      this.http.post<{ items_tagged: number; tags_assigned: number; plugin_id: string }>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/autotag`,
        {
          plugin_id: pluginId,
          min_confidence: minConfidence,
        },
      ),
    );
    await this.loadMediaItems(datasetId);
    await this.loadStratification(datasetId);
    this.showToast(
      `Rule-Based CV Tagger assigned ${res.tags_assigned} tags across ${res.items_tagged} items`,
      'success',
    );
    return res;
  }

  async batchTagItems(
    datasetId: string,
    payload: {
      item_ids: number[];
      add_tags?: Array<{ name: string; category: string; slug?: string }>;
      remove_tag_ids?: number[];
      remove_tag_slugs?: string[];
    },
  ): Promise<{ updated_items: number }> {
    const res = await firstValueFrom(
      this.http.post<{ updated_items: number }>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/items/batch-tag`,
        payload,
      ),
    );
    await this.loadMediaItems(datasetId);
    return res;
  }

  // =========================================================================
  // 5.4 Step 4: OpenRouter Captioning, Skills & Rejection-Guard
  // =========================================================================
  async loadSkills(): Promise<StudioSkill[]> {
    try {
      const res = await firstValueFrom(
        this.http.get<{ skills: StudioSkill[] }>('/api/v1/skills'),
      );
      const list = res?.skills ?? [];
      this.skills.set(list);
      return list;
    } catch {
      return this.skills();
    }
  }

  async saveSkillVersion(payload: {
    slug: string;
    name: string;
    training_type: 'character' | 'concept' | 'style' | 'general';
    system_prompt_template: string;
    user_prompt_template: string;
    glossary_template?: string;
    min_words: number;
    max_words: number;
    discouraged_regexes?: string[];
    require_class_noun?: boolean;
  }): Promise<StudioSkill> {
    const res = await firstValueFrom(
      this.http.post<{ skill: StudioSkill }>('/api/v1/skills', payload),
    );
    await this.loadSkills();
    this.showToast(`Saved "${res.skill.name}" (v${res.skill.version})`, 'success');
    return res.skill;
  }

  async runDatasetCaptioning(
    datasetId: string,
    payload: {
      skill_id?: string | null;
      trigger_word?: string | null;
      trigger_position?: TriggerPosition | null;
      class_noun?: string;
      primary_model?: string | null;
      fallback_model_1?: string | null;
      fallback_model_2?: string | null;
      max_tokens?: number | null;
      temperature?: number | null;
      concurrency?: number;
      only_uncaptioned?: boolean;
    },
  ): Promise<{
    job_id: number;
    status: string;
    items_to_caption: number;
    summary: Record<string, unknown>;
  }> {
    this.showToast('Running 3-Tier OpenRouter Cascade Captioning...', 'info');
    const res = await firstValueFrom(
      this.http.post<{
        job_id: number;
        status: string;
        items_to_caption: number;
        summary: Record<string, unknown>;
      }>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/caption`, payload),
    );
    await this.loadMediaItems(datasetId);
    await this.loadRejections(datasetId);
    await this.refreshDatasets();
    this.showToast(
      `Cascade captioning finished (${res.items_to_caption} items processed)`,
      'success',
    );
    return res;
  }

  async saveManualCaption(
    mediaId: number,
    payload: {
      caption_text: string;
      caption_medium?: string | null;
      caption_short?: string | null;
      enforce_trigger?: boolean;
      trigger_word?: string | null;
      trigger_position?: TriggerPosition | null;
    },
  ): Promise<{
    media_id: number;
    caption_text: string;
    caption_medium: string;
    caption_short: string;
    caption_status: string;
    word_count: number;
    warnings: string[];
  }> {
    const res = await firstValueFrom(
      this.http.put<
        Partial<StudioMediaItem> & {
          media_id?: number;
          caption_text?: string;
          word_count?: number;
          warnings?: string[];
        }
      >(`/api/v1/items/${mediaId}/caption`, {
        caption_text: payload.caption_text,
        caption_medium: payload.caption_medium ?? undefined,
        caption_short: payload.caption_short ?? undefined,
        enforce_trigger: payload.enforce_trigger ?? true,
        trigger_word: payload.trigger_word,
        trigger_position: payload.trigger_position,
      }),
    );

    const savedText = res?.caption_text ?? res?.active_caption ?? payload.caption_text;
    const savedMedium = res?.caption_medium ?? payload.caption_medium ?? '';
    const savedShort = res?.caption_short ?? payload.caption_short ?? '';
    const wordCount = res?.word_count ?? savedText.trim().split(/\s+/).filter(Boolean).length;

    this.mediaItems.update((list) =>
      list.map((it) =>
        it.id === mediaId
          ? {
              ...it,
              active_caption: savedText,
              caption_medium: savedMedium,
              caption_short: savedShort,
              caption_status: 'manual',
              active_caption_model: 'manual',
              active_caption_layer: 'manual',
            }
          : it,
      ),
    );
    void this.refreshDatasets();
    this.showToast(`Saved caption (${wordCount} words)`, 'success');
    return {
      media_id: mediaId,
      caption_text: savedText,
      caption_medium: savedMedium,
      caption_short: savedShort,
      caption_status: 'manual',
      word_count: wordCount,
      warnings: res?.warnings ?? [],
    };
  }

  async approveMediaItem(mediaId: number): Promise<StudioMediaItem | null> {
    this.mediaItems.update((list) =>
      list.map((it) => (it.id === mediaId ? { ...it, caption_status: 'approved' } : it)),
    );
    const res = await firstValueFrom(
      this.http.post<StudioMediaItem>(`/api/v1/items/${mediaId}/approve`, {}),
    );
    if (res && res.id) {
      this.mediaItems.update((list) =>
        list.map((it) => (it.id === mediaId ? { ...it, ...res, caption_status: 'approved' } : it)),
      );
    }
    return res;
  }

  async applySmartCrop(mediaId: number): Promise<StudioMediaItem | null> {
    const res = await firstValueFrom(
      this.http.post<StudioMediaItem>(`/api/v1/items/${mediaId}/smart-crop`, {}),
    );
    this.mediaItems.update((list) =>
      list.map((it) =>
        it.id === mediaId
          ? {
              ...it,
              crop_geometry: res?.crop_geometry ?? {
                x: 0,
                y: 0,
                width: it.width || 1920,
                height: Math.round(((it.height || 1080) * 0.92) / 16) * 16,
              },
            }
          : it,
      ),
    );
    return res;
  }

  async clearSmartCrop(mediaId: number): Promise<StudioMediaItem | null> {
    const res = await firstValueFrom(
      this.http.delete<StudioMediaItem>(`/api/v1/items/${mediaId}/smart-crop`),
    );
    this.mediaItems.update((list) =>
      list.map((it) => (it.id === mediaId ? { ...it, crop_geometry: null } : it)),
    );
    return res;
  }

  async recaptionItem(mediaId: number): Promise<StudioMediaItem | null> {
    const res = await firstValueFrom(
      this.http.post<StudioMediaItem>(`/api/v1/items/${mediaId}/recaption`, {}),
    );
    if (res) {
      this.mediaItems.update((list) =>
        list.map((it) =>
          it.id === mediaId
            ? {
                ...it,
                active_caption: res.active_caption ?? it.active_caption,
                caption_medium: res.caption_medium ?? it.caption_medium,
                caption_short: res.caption_short ?? it.caption_short,
                caption_status: res.caption_status ?? 'vlm_accepted',
                active_caption_layer: res.active_caption_layer ?? 'primary',
              }
            : it,
        ),
      );
    }
    return res;
  }

  async loadRejections(
    datasetId: string,
    mediaId?: number,
    layer?: string,
  ): Promise<RejectionsResponse> {
    let params = new HttpParams();
    if (mediaId !== undefined) params = params.set('media_id', String(mediaId));
    if (layer) params = params.set('layer', layer);
    const res = await firstValueFrom(
      this.http.get<RejectionsResponse>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/rejections`,
        { params },
      ),
    );
    this.rejectionLogs.set(res?.logs ?? []);
    this.needsManualCount.set(res?.needs_manual_count ?? 0);
    return res;
  }

  // =========================================================================
  // 5.5 Step 5A: Standardized Dataset Export
  // =========================================================================
  async exportDataset(
    datasetId: string,
    payload: {
      image_format: 'png' | 'jpg';
      max_edge: number;
      snap_multiple_16: boolean;
      trigger_word?: string | null;
      trigger_position?: TriggerPosition | null;
      create_zip: boolean;
    },
  ): Promise<DatasetExportResponse> {
    const res = await firstValueFrom(
      this.http.post<DatasetExportResponse>(
        `/api/v1/datasets/${encodeURIComponent(datasetId)}/export`,
        payload,
      ),
    );
    this.lastExportResult.set(res);
    await this.refreshDatasets();
    this.showToast(
      `Exported ${res.exported_count} normalized images + captions to ${res.export_dir}`,
      'success',
    );
    return res;
  }

  getExportDownloadUrl(datasetId: string): string {
    return `/api/v1/datasets/${encodeURIComponent(datasetId)}/export/download`;
  }

  // =========================================================================
  // 5.6 Step 5B: Local Non-ML LoRA-RAG & Prompt Studio
  // =========================================================================
  async buildRagIndex(payload: {
    dataset_id?: string | null;
    dense_backend: 'tfidf_svd_128' | 'openrouter_embedding';
    embedding_model?: string | null;
  }): Promise<RagIndexResponse> {
    const res = await firstValueFrom(
      this.http.post<RagIndexResponse>('/api/v1/rag/index', payload),
    );
    this.showToast(
      `Indexed ${res.indexed_documents} captions into FTS5 + ${res.dense_backend} (${res.duration_s.toFixed(2)}s)`,
      'success',
    );
    return res;
  }

  async retrieveRagExemplars(payload: {
    query: string;
    dataset_id?: string | null;
    trigger_word?: string | null;
    training_type?: string | null;
    tag_filters?: Record<string, string[]>;
    top_k?: number;
    diversity?: number;
    retrieval_mode?: 'hybrid' | 'fts5_only' | 'dense_only';
    dense_backend?: 'tfidf_svd_128' | 'openrouter_embedding';
  }): Promise<RagRetrievedExemplar[]> {
    const res = await firstValueFrom(
      this.http.post<{ results: RagRetrievedExemplar[] }>('/api/v1/rag/retrieve', payload),
    );
    return res?.results ?? [];
  }

  async generateLoRAPrompts(payload: {
    query: string;
    selected_tags?: Record<string, string[]>;
    dataset_id?: string | null;
    trigger_word: string;
    style_trigger?: string | null;
    character?: string | null;
    model?: string;
    top_k?: number;
    n_variations?: number;
  }): Promise<RagGenerateResponse> {
    const res = await firstValueFrom(
      this.http.post<RagGenerateResponse>('/api/v1/rag/generate', payload),
    );
    this.showToast('Synthesized 3 grounded Krea 2 prompt archetypes', 'success');
    return res;
  }
}
