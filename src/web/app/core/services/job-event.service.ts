import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { ActiveJob, JobSSEMessage } from '../models/job.model';

export interface ToastAction {
  label: string;
  tab: 'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker';
  videoId?: string | number | null;
}

export interface ToastNotification {
  id: string;
  title: string;
  message: string;
  videoId?: string | number | null;
  isError?: boolean;
  action?: ToastAction | null;
  persistent?: boolean;
}

type JobEventData = NonNullable<JobSSEMessage['data']>;

@Injectable({
  providedIn: 'root',
})
export class JobEventService {
  private readonly api = inject(ApiService);

  readonly activeJobs = signal<Map<number, ActiveJob>>(new Map());
  readonly toasts = signal<ToastNotification[]>([]);
  readonly connectionState = signal<'connecting' | 'connected' | 'disconnected'>('connecting');
  /** Last `tags_updated` event: the image tag model learned something or a suggestion pass finished. */
  readonly tagsUpdated = signal<{ videoId: string | null; at: number } | null>(null);
  private eventSource: EventSource | null = null;

  initSSE(onJobFinished?: (videoId: string, videoName: string) => void): void {
    if (this.eventSource) {
      this.eventSource.close();
    }

    this.connectionState.set('connecting');
    this.eventSource = new EventSource('/api/events');

    this.eventSource.onopen = () => {
      this.connectionState.set('connected');
    };

    this.eventSource.onmessage = (event) => {
      try {
        this.handleMessage(JSON.parse(event.data), onJobFinished);
      } catch (err) {
        console.error('SSE message parsing error:', err);
      }
    };

    this.eventSource.onerror = (err) => {
      this.connectionState.set('disconnected');
      console.warn('SSE connection error:', err);
    };
  }

  private handleMessage(msg: JobSSEMessage, onJobFinished?: (videoId: string, videoName: string) => void): void {
    if (msg.type === 'connected') {
      this.connectionState.set('connected');
      return;
    }
    const d = msg.data;
    if (!d) return;

    if (msg.type === 'tags_updated') {
      this.tagsUpdated.set({ videoId: d.video_id != null ? String(d.video_id) : null, at: Date.now() });
      return;
    }

    this.dispatchJobEvent(msg.type, d, onJobFinished);
  }

  private dispatchJobEvent(
    type: JobSSEMessage['type'],
    d: JobEventData,
    onJobFinished?: (videoId: string, videoName: string) => void
  ): void {
    if (type === 'job_queued' || type === 'job_started' || type === 'job_progress') {
      this.upsertActiveJob(d);
    } else if (type === 'job_finished') {
      this.handleJobFinished(d, onJobFinished);
    } else if (type === 'job_cancelled' || type === 'job_error') {
      this.removeActiveJob(d.job_id);

      if (type === 'job_error') {
        this.showToast('Job Error', d.error || 'Extraction failed', null, true, null, true);
      } else {
        this.showToast('Job Cancelled', `Extraction cancelled for job #${d.job_id}`);
      }
    }
  }

  private upsertActiveJob(d: JobEventData): void {
    this.activeJobs.update((jobs) => {
      const next = new Map(jobs);
      next.set(d.job_id, {
        job_id: d.job_id,
        video_id: d.video_id,
        video_name: d.video_name || 'Video',
        chunk_id: d.chunk_id,
        progress: d.progress || 0,
        step: d.step || 'Processing',
        type: d.job_type || d.type || '',
      });
      return next;
    });
  }

  private removeActiveJob(jobId: number): void {
    this.activeJobs.update((jobs) => {
      const next = new Map(jobs);
      next.delete(jobId);
      return next;
    });
  }

  private handleJobFinished(d: JobEventData, onJobFinished?: (videoId: string, videoName: string) => void): void {
    const finishedJob = this.activeJobs().get(d.job_id);
    const isCaption = (d as any).exported !== undefined || finishedJob?.type === 'openrouter_caption';

    this.removeActiveJob(d.job_id);

    const isTagging = (d as any).suggested !== undefined || finishedJob?.type === 'tag_suggest';
    this.showJobFinishedToast(d, isTagging, isCaption);

    if (onJobFinished) {
      onJobFinished(String(d.video_id), d.video_name || 'Video');
    }
  }

  private showJobFinishedToast(d: JobEventData, isTagging: boolean, isCaption: boolean): void {
    const name = d.video_name || 'Video';
    if (isTagging) {
      this.showToast(
        'Auto-Tagging Complete',
        `${(d as any).suggested ?? 0} tag suggestions for ${name}.`,
        d.video_id,
        false,
        { label: 'Review in Curate', tab: 'curate', videoId: d.video_id }
      );
    } else if (isCaption) {
      this.showToast('Captioning Complete', `${name} has finished batch captioning!`, d.video_id, false, {
        label: 'View Dataset',
        tab: 'dataset',
        videoId: d.video_id,
      });
    } else {
      this.showToast(
        'Extraction Complete',
        `${name} has finished extracting candidates! Ready for curation.`,
        d.video_id,
        false,
        { label: 'Start Curating', tab: 'curate', videoId: d.video_id }
      );
    }
  }

  async cancelJob(jobId: number): Promise<void> {
    try {
      await this.api.delete(`/api/jobs/${jobId}`);
      this.activeJobs.update((jobs) => {
        const next = new Map(jobs);
        next.delete(jobId);
        return next;
      });
    } catch (err) {
      console.error(`Failed to cancel job #${jobId}:`, err);
    }
  }

  showToast(
    title: string,
    message: string,
    videoId: string | number | null = null,
    isError = false,
    action: ToastAction | null = null,
    persistent = false
  ): void {
    const id = `${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    const shouldPersist = isError || persistent;
    const toast: ToastNotification = { id, title, message, videoId, isError, action, persistent: shouldPersist };
    this.toasts.update((list) => [...list, toast]);

    if (!shouldPersist) {
      setTimeout(() => {
        this.dismissToast(id);
      }, 9000);
    }
  }

  dismissToast(id: string): void {
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }
}
