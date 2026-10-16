import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { ApiError } from './api-error';
import { SettingsService } from './settings.service';
import { LearnedWeightsStatus } from '../models/scoring.model';
import { PipelineSettings } from '../models/settings.model';

/**
 * Phase F learned quality weights: status (`GET /api/scoring/learned-weights`), fit (`POST .../fit`) and the
 * opt-in `use_learned_weights` setting (partial `PUT /api/settings`, so no other setting is round-tripped).
 */
@Injectable({
  providedIn: 'root',
})
export class ScoringService {
  private readonly api = inject(ApiService);
  private readonly settingsService = inject(SettingsService);

  readonly learnedWeights = signal<LearnedWeightsStatus | null>(null);
  readonly isLoading = signal(false);
  readonly isFitting = signal(false);
  readonly isSaving = signal(false);
  readonly error = signal<string | null>(null);

  async loadLearnedWeights(): Promise<LearnedWeightsStatus | null> {
    this.isLoading.set(true);
    try {
      const status = await this.api.get<LearnedWeightsStatus>('/api/scoring/learned-weights');
      this.learnedWeights.set(status);
      this.error.set(null);
      return status;
    } catch (err) {
      this.error.set(this.message(err, 'Could not load the learned weights'));
      return null;
    } finally {
      this.isLoading.set(false);
    }
  }

  /** Fits on the logged keep/reject events; 400 (too few events / one class) lands in `error`. */
  async fitLearnedWeights(): Promise<boolean> {
    if (this.isFitting()) return false;
    this.isFitting.set(true);
    try {
      this.learnedWeights.set(await this.api.post<LearnedWeightsStatus>('/api/scoring/learned-weights/fit'));
      this.error.set(null);
      return true;
    } catch (err) {
      this.error.set(this.message(err, 'Fitting the learned weights failed'));
      return false;
    } finally {
      this.isFitting.set(false);
    }
  }

  /** Toggles `use_learned_weights` (partial PUT) and refreshes the status (`active` depends on it). */
  async setUseLearnedWeights(enabled: boolean): Promise<boolean> {
    this.isSaving.set(true);
    try {
      const res = await this.api.put<{ settings?: PipelineSettings }>('/api/settings', { use_learned_weights: enabled });
      if (res?.settings) {
        this.settingsService.settings.set(res.settings);
      } else {
        this.settingsService.settings.update((s) => ({ ...s, use_learned_weights: enabled }));
      }
      this.learnedWeights.update((s) => (s ? { ...s, use_learned_weights: enabled } : s));
      this.error.set(null);
      await this.loadLearnedWeights();
      return true;
    } catch (err) {
      this.error.set(this.message(err, 'Could not save the setting'));
      return false;
    } finally {
      this.isSaving.set(false);
    }
  }

  private message(err: unknown, fallback: string): string {
    if (err instanceof ApiError) return err.message || fallback;
    return err instanceof Error ? err.message : fallback;
  }
}
