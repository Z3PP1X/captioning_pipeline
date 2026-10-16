import { Injectable, InjectionToken, computed, inject, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { ApiService } from './api.service';
import { ApiError } from './api-error';
import {
  ArchetypeKey,
  CharacterProfile,
  CuratedPreset,
  PromptArchetype,
  PromptSeedFrame,
  RagGenerateResponse,
  RagGenerateResult,
  RagHistoryEntry,
  RagHistoryList,
  RagIndexStatus,
  RagItemPreview,
  RagLintResponse,
  RagRetrieveResponse,
  RagSimilarResponse,
  SelectedTags,
  TaxonomyCategory,
} from '../models/prompt-rag.model';
import { FrameCaptionResponse } from '../models/caption.model';
import { buildHashRoute } from '../utils/hash-route.utils';
import { captionToSeedDescription, similarErrorMessage } from '../utils/prompt-seed.utils';

const STATUS_POLL_MS = 1000;
const HISTORY_PAGE = 50;
export const BATCH_SIZES = [1, 3, 5, 10] as const;
export const SIMILAR_TOP_K = 8;

/**
 * Opens the Prompt Director tab. Default: sets the hash route `#/prompt-clicker` (AppComponent's hashchange
 * handler switches the tab); tests override the token.
 */
export const PROMPT_DIRECTOR_NAVIGATE = new InjectionToken<() => void>('PROMPT_DIRECTOR_NAVIGATE', {
  providedIn: 'root',
  factory: () => {
    const doc = inject(DOCUMENT);
    return () => {
      const win = doc.defaultView;
      const hash = buildHashRoute('prompt-clicker', null);
      if (win && win.location.hash !== hash) win.location.hash = hash;
    };
  },
});

@Injectable({
  providedIn: 'root',
})
export class PromptRagService {
  private readonly api = inject(ApiService);
  private readonly navigateToDirector = inject(PROMPT_DIRECTOR_NAVIGATE);

  readonly categories = signal<Record<string, TaxonomyCategory>>({});
  readonly presets = signal<CuratedPreset[]>([]);
  readonly characters = signal<Record<string, CharacterProfile>>({});
  readonly selectedCharacter = signal<string | null>(null);

  readonly selectedTags = signal<SelectedTags>({
    equipment: [],
    surroundings: [],
    position: [],
    camera_lighting: [],
  });
  readonly userDescription = signal<string>('');
  readonly selectedModel = signal<string>('google/gemma-4-31b-it');
  readonly selectedTrigger = signal<string>('restrained_elegance');
  readonly localUrl = signal<string>('http://localhost:11434');

  readonly isGenerating = signal<boolean>(false);
  readonly isRetrieving = signal<boolean>(false);
  /** Batch "Generate N" size (1-10). */
  readonly batchSize = signal<number>(1);
  /** Variations of the last generate call (or one history entry opened from the history panel). */
  readonly batch = signal<RagHistoryEntry[]>([]);
  readonly activeIndex = signal<number>(0);
  readonly activeEntry = computed<RagHistoryEntry | null>(() => this.batch()[this.activeIndex()] ?? this.batch()[0] ?? null);
  readonly generationResult = computed<RagGenerateResult | null>(() => this.activeEntry()?.result ?? null);
  readonly retrievedContext = signal<RagItemPreview[]>([]);
  readonly errorMessage = signal<string | null>(null);

  readonly indexStatus = signal<RagIndexStatus | null>(null);
  readonly statusError = signal<string | null>(null);
  readonly isIndexReady = computed(() => this.indexStatus()?.ready ?? false);
  readonly isIndexBuilding = computed(() => this.indexStatus()?.build.state === 'running');
  private statusTimer: ReturnType<typeof setTimeout> | null = null;

  /** Persisted history (`rag_history`), newest first. */
  readonly history = signal<RagHistoryEntry[]>([]);
  readonly historyTotal = signal<number>(0);
  readonly historyFavoritesOnly = signal<boolean>(false);
  readonly isHistoryLoading = signal<boolean>(false);
  readonly historyError = signal<string | null>(null);

  /** Frame whose caption prefilled the description ("Use as prompt seed" from Curate / Dataset). */
  readonly seedFrame = signal<PromptSeedFrame | null>(null);
  readonly isSeeding = signal<boolean>(false);

  /** "More like this frame": the Prompt Director loads `GET /api/rag/similar` for this frame. */
  readonly seedFrameId = signal<number | null>(null);
  readonly seedFrameLabel = signal<string | null>(null);
  readonly similarResults = signal<RagItemPreview[]>([]);
  /** null = not loaded yet; false = no image index (409 or `index_ready: false`). */
  readonly similarIndexReady = signal<boolean | null>(null);
  readonly similarEmbedder = signal<string | null>(null);
  readonly isLoadingSimilar = signal<boolean>(false);
  readonly similarError = signal<string | null>(null);
  /** Frame id the similar results (or error) belong to; differs from `seedFrameId` while a load is pending. */
  readonly similarLoadedFor = signal<number | null>(null);

  readonly totalSelectedCount = computed(() => {
    const tags = this.selectedTags();
    return Object.values(tags).reduce((sum, list) => sum + (list?.length || 0), 0);
  });

  async loadMetadata(): Promise<void> {
    try {
      const [catRes, preRes, charRes] = await Promise.all([
        this.api.get<{ categories: Record<string, TaxonomyCategory> }>('/api/rag/categories'),
        this.api.get<{ presets: CuratedPreset[] }>('/api/rag/presets'),
        this.api.get<{ characters: Record<string, CharacterProfile> }>('/api/rag/characters'),
      ]);
      this.categories.set(catRes.categories);
      this.presets.set(preRes.presets);
      this.characters.set(charRes.characters);
    } catch (err: any) {
      console.error('Failed to load RAG metadata:', err);
      this.errorMessage.set(err?.message || 'Failed to load categories');
    }
  }

  async loadStatus(): Promise<void> {
    try {
      const status = await this.api.get<RagIndexStatus>('/api/rag/status');
      this.applyStatus(status);
      this.statusError.set(null);
    } catch (err: any) {
      console.error('Failed to load RAG index status:', err);
      this.statusError.set(err?.message || 'Failed to load RAG index status');
    }
  }

  async buildIndex(): Promise<void> {
    this.statusError.set(null);
    try {
      this.applyStatus(await this.api.post<RagIndexStatus>('/api/rag/index', {}));
    } catch (err: any) {
      // 409 = already running: just resume polling
      this.statusError.set(err?.message || 'Failed to start index build');
      await this.loadStatus();
    }
  }

  stopStatusPolling(): void {
    if (this.statusTimer) {
      clearTimeout(this.statusTimer);
      this.statusTimer = null;
    }
  }

  private applyStatus(status: RagIndexStatus): void {
    this.indexStatus.set(status);
    this.stopStatusPolling();
    if (status.build.state === 'running') {
      this.statusTimer = setTimeout(() => this.loadStatus(), STATUS_POLL_MS);
    }
  }

  selectCharacter(characterId: string | null): void {
    this.selectedCharacter.set(characterId);
  }

  isTagSelected(category: string, tagId: string): boolean {
    const list = this.selectedTags()[category] || [];
    return list.includes(tagId);
  }

  toggleTag(category: string, tagId: string): void {
    const current = { ...this.selectedTags() };
    const list = current[category] ? [...current[category]] : [];
    const idx = list.indexOf(tagId);
    if (idx >= 0) {
      list.splice(idx, 1);
    } else {
      list.push(tagId);
    }
    current[category] = list;
    this.selectedTags.set(current);
  }

  clearSelections(): void {
    this.selectedTags.set({
      equipment: [],
      surroundings: [],
      position: [],
      camera_lighting: [],
    });
    this.selectedCharacter.set(null);
    this.userDescription.set('');
    this.seedFrame.set(null);
    this.batch.set([]);
    this.activeIndex.set(0);
    this.errorMessage.set(null);
  }

  /** Empties one taxonomy category. */
  clearCategory(category: string): void {
    if (!(this.selectedTags()[category]?.length)) return;
    this.selectedTags.update((tags) => ({ ...tags, [category]: [] }));
  }

  // ------------------------------------------------------------------ seeds from Curate / Dataset
  /**
   * "Use as prompt seed": loads the frame's caption (`GET /api/frames/{id}/caption`), puts it (without the
   * trigger token) into the description and opens the Prompt Director. Returns an error message for the
   * caller's toast, or null on success.
   */
  async useFrameAsSeed(frameId: number, label: string | null = null): Promise<string | null> {
    this.isSeeding.set(true);
    try {
      const res = await this.api.get<FrameCaptionResponse>(`/api/frames/${frameId}/caption`);
      const text = captionToSeedDescription(res.caption);
      if (!text) {
        return `Frame #${frameId} has no caption yet. Caption it first, or use "More like this frame".`;
      }
      this.userDescription.set(text);
      this.seedFrame.set({ frameId, label });
      this.errorMessage.set(null);
      this.navigateToDirector();
      return null;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return `Frame #${frameId} no longer exists.`;
      return err instanceof Error ? err.message : String(err);
    } finally {
      this.isSeeding.set(false);
    }
  }

  /** "More like this frame": remembers the frame and opens the Prompt Director, which then calls `loadSimilar()`. */
  showSimilarTo(frameId: number, label: string | null = null): void {
    if (this.seedFrameId() !== frameId) {
      this.similarResults.set([]);
      this.similarIndexReady.set(null);
      this.similarEmbedder.set(null);
      this.similarError.set(null);
      this.similarLoadedFor.set(null);
    }
    this.seedFrameId.set(frameId);
    this.seedFrameLabel.set(label);
    this.navigateToDirector();
  }

  /** `GET /api/rag/similar?frame_id=<seedFrameId>&top_k=8`. 409 = no image index yet (not an error). */
  async loadSimilar(topK = SIMILAR_TOP_K): Promise<void> {
    const frameId = this.seedFrameId();
    if (frameId === null) return;
    this.isLoadingSimilar.set(true);
    this.similarError.set(null);
    try {
      const res = await this.api.get<RagSimilarResponse>(`/api/rag/similar?frame_id=${frameId}&top_k=${topK}`);
      if (this.seedFrameId() !== frameId) return; // another frame was requested meanwhile
      this.similarResults.set(res.results ?? []);
      this.similarIndexReady.set(res.index_ready !== false);
      this.similarEmbedder.set(res.embedder ?? null);
    } catch (err) {
      if (this.seedFrameId() !== frameId) return;
      this.similarResults.set([]);
      const msg = similarErrorMessage(err, frameId);
      if (msg === null) {
        this.similarIndexReady.set(false);
      } else {
        this.similarError.set(msg);
      }
    } finally {
      if (this.seedFrameId() === frameId) this.similarLoadedFor.set(frameId);
      this.isLoadingSimilar.set(false);
    }
  }

  clearSimilar(): void {
    this.seedFrameId.set(null);
    this.seedFrameLabel.set(null);
    this.similarResults.set([]);
    this.similarIndexReady.set(null);
    this.similarEmbedder.set(null);
    this.similarError.set(null);
    this.similarLoadedFor.set(null);
  }

  applyPreset(preset: CuratedPreset): void {
    this.selectedTags.set({
      equipment: [...(preset.tags['equipment'] || [])],
      surroundings: [...(preset.tags['surroundings'] || [])],
      position: [...(preset.tags['position'] || [])],
      camera_lighting: [...(preset.tags['camera_lighting'] || [])],
    });
    if (preset.suggested_description) {
      this.userDescription.set(preset.suggested_description);
    }
  }

  async retrieveContext(): Promise<void> {
    this.isRetrieving.set(true);
    this.errorMessage.set(null);
    try {
      const res = await this.api.post<RagRetrieveResponse>('/api/rag/retrieve', {
        selected_tags: this.selectedTags(),
        user_description: this.userDescription(),
        top_k: 4,
      });
      this.retrievedContext.set(res.results);
      if (!res.index_ready) {
        void this.loadStatus();
      }
    } catch (err: any) {
      console.error('Failed to retrieve context:', err);
      this.errorMessage.set(err?.message || 'Failed to retrieve context');
    } finally {
      this.isRetrieving.set(false);
    }
  }

  async generatePrompts(): Promise<void> {
    this.isGenerating.set(true);
    this.errorMessage.set(null);
    try {
      const res = await this.api.post<RagGenerateResponse>('/api/rag/generate', {
        selected_tags: this.selectedTags(),
        user_description: this.userDescription(),
        character: this.selectedCharacter() || undefined,
        top_k: 4,
        n: this.batchSize(),
        model: this.selectedModel(),
        trigger: this.selectedTrigger(),
        local_url: this.selectedModel() === 'local' ? this.localUrl() : undefined,
        // No api_key: the server uses the key stored via PUT /api/settings (or OPENROUTER_API_KEY)
      });
      const variations = res.variations?.length ? res.variations : [this.entryFromLegacy(res)];
      // Top-level notices may carry batch-level info (e.g. failed variations)
      variations[0] = { ...variations[0], result: { ...variations[0].result, notices: res.notices ?? [] } };
      this.batch.set(variations);
      this.activeIndex.set(0);
      this.retrievedContext.set(res.rag_context ?? []);
      void this.loadHistory();
    } catch (err: any) {
      console.error('Failed to generate prompts:', err);
      this.errorMessage.set(err?.message || 'Failed to generate prompts');
    } finally {
      this.isGenerating.set(false);
    }
  }

  selectVariation(index: number): void {
    const entry = this.batch()[index];
    if (!entry) return;
    this.activeIndex.set(index);
    this.retrievedContext.set(entry.result.rag_context ?? []);
  }

  /** Old servers (before history) return only the single-result shape. */
  private entryFromLegacy(res: RagGenerateResult): RagHistoryEntry {
    return {
      id: null,
      created_at: Date.now() / 1000,
      favorite: false,
      batch_id: null,
      batch_index: 0,
      inputs: {
        selected_tags: this.selectedTags(),
        user_description: this.userDescription(),
        character: this.selectedCharacter(),
        model: this.selectedModel(),
        trigger: this.selectedTrigger(),
        top_k: 4,
        n: 1,
        local_url: null,
      },
      result: res,
    };
  }

  // ------------------------------------------------------------------ history + favourites
  async loadHistory(): Promise<void> {
    this.isHistoryLoading.set(true);
    try {
      const fav = this.historyFavoritesOnly() ? '&favorites=1' : '';
      const res = await this.api.get<RagHistoryList>(`/api/rag/history?limit=${HISTORY_PAGE}${fav}`);
      this.history.set(res.items);
      this.historyTotal.set(res.total);
      this.historyError.set(null);
    } catch (err: any) {
      console.error('Failed to load prompt history:', err);
      this.historyError.set(err?.message || 'Failed to load prompt history');
    } finally {
      this.isHistoryLoading.set(false);
    }
  }

  setHistoryFavoritesOnly(value: boolean): void {
    if (this.historyFavoritesOnly() === value) return;
    this.historyFavoritesOnly.set(value);
    void this.loadHistory();
  }

  async toggleFavorite(entry: RagHistoryEntry): Promise<void> {
    if (entry.id == null) return;
    try {
      const updated = await this.api.patch<RagHistoryEntry>(`/api/rag/history/${entry.id}`, { favorite: !entry.favorite });
      this.replaceEntry(updated);
      if (this.historyFavoritesOnly() && !updated.favorite) {
        this.history.update((list) => list.filter((e) => e.id !== updated.id));
        this.historyTotal.update((t) => Math.max(0, t - 1));
      }
    } catch (err: any) {
      this.historyError.set(err?.message || 'Failed to update favourite');
    }
  }

  async deleteEntry(entry: RagHistoryEntry): Promise<void> {
    if (entry.id == null) return;
    try {
      await this.api.delete(`/api/rag/history/${entry.id}`);
      this.history.update((list) => list.filter((e) => e.id !== entry.id));
      this.historyTotal.update((t) => Math.max(0, t - 1));
      // Keep the prompts on screen, but they are no longer persisted
      this.batch.update((list) => list.map((e) => (e.id === entry.id ? { ...e, id: null, favorite: false } : e)));
    } catch (err: any) {
      this.historyError.set(err?.message || 'Failed to delete history entry');
    }
  }

  /** Show a history entry in the result pane. */
  openEntry(entry: RagHistoryEntry): void {
    this.batch.set([entry]);
    this.activeIndex.set(0);
    this.retrievedContext.set(entry.result.rag_context ?? []);
  }

  /** Put a history entry's inputs back into the editor (tags, description, character, model, trigger). */
  restoreInputs(entry: RagHistoryEntry): void {
    const tags = entry.inputs?.selected_tags ?? {};
    this.selectedTags.set({
      equipment: [...(tags['equipment'] || [])],
      surroundings: [...(tags['surroundings'] || [])],
      position: [...(tags['position'] || [])],
      camera_lighting: [...(tags['camera_lighting'] || [])],
    });
    this.userDescription.set(entry.inputs?.user_description ?? '');
    this.selectedCharacter.set(entry.inputs?.character ?? null);
    if (entry.inputs?.model) this.selectedModel.set(entry.inputs.model);
    if (entry.inputs?.trigger) this.selectedTrigger.set(entry.inputs.trigger);
    if (entry.inputs?.local_url) this.localUrl.set(entry.inputs.local_url);
  }

  // ------------------------------------------------------------------ inline edit + re-lint
  /** Re-lint an edited prompt and apply it to the entry (persisted when the entry is in history). */
  async applyEdit(entry: RagHistoryEntry, key: ArchetypeKey, text: string): Promise<PromptArchetype | null> {
    try {
      const lint = await this.api.post<RagLintResponse>('/api/rag/lint', {
        prompt: text,
        archetype: key,
        trigger: entry.result.trigger,
        character: entry.result.character ?? undefined,
      });
      const prev = entry.result.archetypes[key];
      const arch: PromptArchetype = {
        name: prev?.name ?? key.toUpperCase(),
        prompt: lint.clean_prompt,
        token_count: lint.token_count,
        warnings: lint.warnings,
        is_valid: lint.is_valid,
        edited: true,
      };
      let updated: RagHistoryEntry = {
        ...entry,
        result: { ...entry.result, archetypes: { ...entry.result.archetypes, [key]: arch } },
      };
      if (entry.id != null) {
        updated = await this.api.patch<RagHistoryEntry>(`/api/rag/history/${entry.id}`, {
          prompts: { [key]: lint.clean_prompt },
        });
      }
      this.replaceEntry(updated, entry);
      return updated.result.archetypes[key];
    } catch (err: any) {
      this.errorMessage.set(err?.message || 'Failed to re-lint prompt');
      return null;
    }
  }

  /** Swap an entry (matched by id, or by identity for unsaved ones) in the batch and the history list. */
  private replaceEntry(updated: RagHistoryEntry, original?: RagHistoryEntry): void {
    const same = (e: RagHistoryEntry) => (updated.id != null ? e.id === updated.id : e === original);
    this.batch.update((list) => list.map((e) => (same(e) ? updated : e)));
    this.history.update((list) => list.map((e) => (same(e) ? updated : e)));
  }
}
