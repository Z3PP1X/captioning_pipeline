import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
export type {
  AutopilotConfig,
  AutopilotMetrics,
  AutopilotRefinePayload,
  AutopilotRefineResponse,
  AutopilotStage,
  AutopilotStartPayload,
  AutopilotStateData,
  AutopilotStatusResponse,
  ChunkPriority,
} from '../models/autopilot.model';
import {
  AutopilotRefinePayload,
  AutopilotRefineResponse,
  AutopilotStartPayload,
  AutopilotStatusResponse,
  ChunkPriority,
} from '../models/autopilot.model';

export const PRIORITY_WEIGHTS: Record<ChunkPriority, number> = {
  skip: 0.0,
  low: 0.4,
  normal: 1.0,
  high: 2.0,
};

@Injectable({
  providedIn: 'root',
})
export class AutopilotService {
  private readonly api = inject(ApiService);

  readonly status = signal<AutopilotStatusResponse | null>(null);
  readonly isPlanning = signal<boolean>(false);
  readonly isApproving = signal<boolean>(false);
  readonly isRefining = signal<boolean>(false);

  readonly stage = computed(() => this.status()?.autopilot?.stage || 'idle');
  readonly metrics = computed(() => this.status()?.metrics || null);
  readonly isAwaitingReview = computed(() => this.stage() === 'awaiting_review');
  readonly isExtracting = computed(() => this.stage() === 'extracting');
  readonly isReady = computed(() => this.stage() === 'ready');

  async loadAutopilot(videoId: string | number): Promise<AutopilotStatusResponse | null> {
    try {
      const data = await this.api.get<AutopilotStatusResponse>(`/api/videos/${videoId}/autopilot`);
      this.status.set(data);
      return data;
    } catch (err) {
      console.error(`Failed to load autopilot status for video ${videoId}:`, err);
      this.status.set(null);
      return null;
    }
  }

  async startAutopilot(
    videoId: string | number,
    payload: AutopilotStartPayload
  ): Promise<AutopilotStatusResponse> {
    this.isPlanning.set(true);
    try {
      const data = await this.api.post<AutopilotStatusResponse>(
        `/api/videos/${videoId}/autopilot/start`,
        payload
      );
      this.status.set(data);
      return data;
    } finally {
      this.isPlanning.set(false);
    }
  }

  async updatePriorities(
    videoId: string | number,
    priorities: Record<string | number, ChunkPriority>
  ): Promise<AutopilotStatusResponse> {
    const data = await this.api.post<AutopilotStatusResponse>(
      `/api/videos/${videoId}/autopilot/priorities`,
      { priorities }
    );
    this.status.set(data);
    return data;
  }

  async approve(
    videoId: string | number,
    priorities?: Record<string | number, ChunkPriority>
  ): Promise<AutopilotStatusResponse> {
    this.isApproving.set(true);
    try {
      const data = await this.api.post<AutopilotStatusResponse>(
        `/api/videos/${videoId}/autopilot/approve`,
        { priorities }
      );
      this.status.set(data);
      return data;
    } finally {
      this.isApproving.set(false);
    }
  }

  async refine(
    videoId: string | number,
    payload: AutopilotRefinePayload = {}
  ): Promise<AutopilotRefineResponse> {
    this.isRefining.set(true);
    try {
      const data = await this.api.post<AutopilotRefineResponse>(
        `/api/videos/${videoId}/autopilot/refine`,
        payload
      );
      // Refresh status after refine
      await this.loadAutopilot(videoId);
      return data;
    } finally {
      this.isRefining.set(false);
    }
  }

  async reset(videoId: string | number): Promise<void> {
    await this.api.post(`/api/videos/${videoId}/autopilot/reset`);
    await this.loadAutopilot(videoId);
  }
}
