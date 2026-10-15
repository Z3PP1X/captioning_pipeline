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
        return settingsRes.settings;
      }
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
    return this.settings();
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

  async saveSettings(updated: Partial<PipelineSettings>): Promise<void> {
    try {
      const current = this.settings();
      const payload = { ...current, ...updated };
      await this.api.put('/api/settings', payload);
      this.settings.set(payload);
    } catch (err) {
      console.error('Failed to save settings:', err);
      throw err;
    }
  }
}
