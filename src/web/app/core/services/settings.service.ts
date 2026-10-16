import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { PipelineSettings } from '../models/settings.model';

export interface OpenRouterModelOption {
  id: string;
  name: string;
  recommended: boolean;
}

const DEFAULT_MODELS: OpenRouterModelOption[] = [
  { id: 'google/gemma-4-31b-it', name: 'Google Gemma 4 31B (Default)', recommended: true },
  { id: 'deepseek/deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash (Layer 2 Fallback)', recommended: false },
  { id: 'x-ai/grok-4.7', name: 'Grok 4.7 (Layer 3 Final Fallback, Uncensored)', recommended: false },
  { id: 'qwen/qwen-2.5-vl-72b-instruct', name: 'Qwen 2.5 VL 72B', recommended: false },
  { id: 'google/gemini-2.0-flash-exp:free', name: 'Gemini 2.0 Flash (Free)', recommended: false },
  { id: 'mistralai/pixtral-12b', name: 'Pixtral 12B', recommended: false },
];

/** Browser cache of the OpenRouter key used by older builds; migrated to the server and removed (F7). */
export const LEGACY_OPENROUTER_KEY_STORAGE = 'cp_openrouter_api_key';

@Injectable({
  providedIn: 'root',
})
export class SettingsService {
  private readonly api = inject(ApiService);

  readonly settings = signal<PipelineSettings>({
    default_k: 5,
    default_rule: 'person',
    sample_fps: 4.0,
    survivor_frac: 0.5,
    trigger_word: '',
    max_edge: 2048,
  });

  readonly openRouterModels = signal<OpenRouterModelOption[]>(DEFAULT_MODELS);

  async loadSettings(): Promise<PipelineSettings> {
    try {
      const [settingsRes] = await Promise.all([
        this.api.get<{ settings: PipelineSettings }>('/api/settings'),
        this.loadOpenRouterModels(),
      ]);
      if (settingsRes.settings) {
        this.settings.set(settingsRes.settings);
        await this.migrateLegacyOpenRouterKey();
        return this.settings();
      }
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
    return this.settings();
  }

  /**
   * F7 (decided: the key lives on the server only). A key cached in localStorage by an older build is moved to
   * the server once (unless one is stored there already) and then deleted from the browser.
   */
  async migrateLegacyOpenRouterKey(): Promise<void> {
    let storage: Storage | undefined;
    try {
      storage = globalThis.localStorage;
    } catch {
      return;
    }
    const legacy = storage?.getItem(LEGACY_OPENROUTER_KEY_STORAGE)?.trim();
    if (!storage || !legacy) return;
    try {
      if (!this.settings().openrouter_api_key_set) {
        await this.saveOpenRouterKey(legacy);
      }
      storage.removeItem(LEGACY_OPENROUTER_KEY_STORAGE);
    } catch (err) {
      console.warn('Could not move the cached OpenRouter key to the server; keeping it for now:', err);
    }
  }

  async loadOpenRouterModels(): Promise<OpenRouterModelOption[]> {
    try {
      const data = await this.api.get<{ models: OpenRouterModelOption[] }>('/api/openrouter/models');
      if (data.models && data.models.length > 0) {
        this.openRouterModels.set(data.models);
        return data.models;
      }
    } catch (err) {
      console.warn('Failed to load OpenRouter models, using defaults:', err);
    }
    return this.openRouterModels();
  }

  /**
   * Stores the OpenRouter key on the server only (product decision: no browser persistence). The response is
   * redacted, so the secret never lands in the settings signal; only `openrouter_api_key_set` does.
   */
  async saveOpenRouterKey(key: string): Promise<void> {
    const trimmed = key.trim();
    if (!trimmed) return;
    const res = await this.api.put<{ settings?: PipelineSettings }>('/api/settings', { openrouter_api_key: trimmed });
    if (res?.settings) {
      this.settings.set(res.settings);
    } else {
      this.settings.update((s) => ({ ...s, openrouter_api_key_set: true }));
    }
  }

  async saveSettings(updated: Partial<PipelineSettings>): Promise<void> {
    try {
      // Read-only / secret fields are not round-tripped
      const { openrouter_api_key_set, openrouter_api_key, ...current } = this.settings();
      const payload = { ...current, ...updated };
      delete payload.openrouter_api_key_set;
      const res = await this.api.put<{ settings?: PipelineSettings }>('/api/settings', payload);
      this.settings.set(res?.settings ?? { ...payload, openrouter_api_key_set, openrouter_api_key: undefined });
    } catch (err) {
      console.error('Failed to save settings:', err);
      throw err;
    }
  }
}
