import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import {
  CharacterProfile,
  CuratedPreset,
  PromptArchetype,
  RagGenerateResponse,
  RagItemPreview,
  SelectedTags,
  TaxonomyCategory,
} from '../models/prompt-rag.model';

@Injectable({
  providedIn: 'root',
})
export class PromptRagService {
  private readonly api = inject(ApiService);

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
  readonly generationResult = signal<RagGenerateResponse | null>(null);
  readonly retrievedContext = signal<RagItemPreview[]>([]);
  readonly errorMessage = signal<string | null>(null);

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
    this.generationResult.set(null);
    this.errorMessage.set(null);
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
      const res = await this.api.post<{ results: RagItemPreview[] }>('/api/rag/retrieve', {
        selected_tags: this.selectedTags(),
        user_description: this.userDescription(),
        top_k: 4,
      });
      this.retrievedContext.set(res.results);
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
        model: this.selectedModel(),
        trigger: this.selectedTrigger(),
        local_url: this.selectedModel() === 'local' ? this.localUrl() : undefined,
      });
      this.generationResult.set(res);
      if (res.rag_context) {
        this.retrievedContext.set(res.rag_context);
      }
    } catch (err: any) {
      console.error('Failed to generate prompts:', err);
      this.errorMessage.set(err?.message || 'Failed to generate prompts');
    } finally {
      this.isGenerating.set(false);
    }
  }
}
