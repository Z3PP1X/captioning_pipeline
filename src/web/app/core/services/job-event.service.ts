import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { ActiveJob, JobSSEMessage } from '../models/job.model';

export interface ToastNotification {
  id: string;
  title: string;
  message: string;
  videoId?: string | number | null;
  isError?: boolean;
}

@Injectable({
  providedIn: 'root',
})
export class JobEventService {
  private readonly api = inject(ApiService);

  readonly activeJobs = signal<Map<number, ActiveJob>>(new Map());
  readonly toasts = signal<ToastNotification[]>([]);
  private eventSource: EventSource | null = null;

  initSSE(onJobFinished?: (videoId: string, videoName: string) => void): void {
    if (this.eventSource) {
      this.eventSource.close();
    }

    this.eventSource = new EventSource('/api/events');

    this.eventSource.onmessage = (event) => {
      try {
        const msg: JobSSEMessage = JSON.parse(event.data);
        const d = msg.data;

        if (
          msg.type === 'job_queued' ||
          msg.type === 'job_started' ||
          msg.type === 'job_progress'
        ) {
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
        } else if (msg.type === 'job_finished') {
          const finishedJob = this.activeJobs().get(d.job_id);
          const isCaption = (d as any).exported !== undefined || finishedJob?.type === 'openrouter_caption';

          this.activeJobs.update((jobs) => {
            const next = new Map(jobs);
            next.delete(d.job_id);
            return next;
          });

          if (isCaption) {
            this.showToast(
              'Captioning Complete',
              `${d.video_name || 'Video'} has finished batch captioning!`,
              d.video_id
            );
          } else {
            this.showToast(
              'Extraction Complete',
              `${d.video_name || 'Video'} has finished extracting candidates! Ready for curation.`,
              d.video_id
            );
          }

          if (onJobFinished) {
            onJobFinished(String(d.video_id), d.video_name || 'Video');
          }
        } else if (msg.type === 'job_cancelled' || msg.type === 'job_error') {
          this.activeJobs.update((jobs) => {
            const next = new Map(jobs);
            next.delete(d.job_id);
            return next;
          });

          if (msg.type === 'job_error') {
            this.showToast('Job Error', d.error || 'Extraction failed', null, true);
          } else {
            this.showToast('Job Cancelled', `Extraction cancelled for job #${d.job_id}`);
          }
        }
      } catch (err) {
        console.error('SSE message parsing error:', err);
      }
    };

    this.eventSource.onerror = (err) => {
      console.warn('SSE connection error:', err);
    };
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

  showToast(title: string, message: string, videoId: string | number | null = null, isError = false): void {
    const id = `${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    const toast: ToastNotification = { id, title, message, videoId, isError };
    this.toasts.update((list) => [...list, toast]);

    setTimeout(() => {
      this.dismissToast(id);
    }, 9000);
  }

  dismissToast(id: string): void {
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }
}
