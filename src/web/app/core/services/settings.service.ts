import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { PipelineSettings } from '../models/settings.model';

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

  async loadSettings(): Promise<PipelineSettings> {
    try {
      const data = await this.api.get<{ settings: PipelineSettings }>('/api/settings');
      if (data.settings) {
        this.settings.set(data.settings);
        return data.settings;
      }
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
    return this.settings();
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
