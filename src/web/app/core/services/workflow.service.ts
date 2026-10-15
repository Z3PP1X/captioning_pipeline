import { Injectable, computed, inject, signal } from '@angular/core';
import { VideoService } from './video.service';
import { ChunkService } from './chunk.service';
import { CurateService } from './curate.service';
import { JobEventService } from './job-event.service';
import { Video } from '../models/video.model';

export type WorkflowStepId = 'chunk' | 'extract' | 'curate' | 'caption' | 'done';

export type StepState = 'locked' | 'current' | 'done' | 'running' | 'error';

export interface WorkflowStepInfo {
  id: WorkflowStepId;
  stepNumber: number;
  label: string;
  state: StepState;
  reason?: string;
}

export interface NextActionInfo {
  label: string;
  actionId: 'auto_suggest' | 'save_chunks' | 'normalise_budget' | 'extract' | 'curate' | 'caption' | 'next_video' | 'restore';
  disabled: boolean;
  whyDisabled?: string;
  isPrimary: boolean;
}

@Injectable({
  providedIn: 'root',
})
export class WorkflowService {
  private readonly videoService = inject(VideoService);
  private readonly chunkService = inject(ChunkService);
  private readonly curateService = inject(CurateService);
  private readonly jobEvents = inject(JobEventService);

  readonly currentVideo = this.videoService.currentVideo;

  readonly activeJobForCurrentVideo = computed(() => {
    const video = this.currentVideo();
    if (!video) return null;
    for (const job of this.jobEvents.activeJobs().values()) {
      if (String(job.video_id) === String(video.id)) {
        return job;
      }
    }
    return null;
  });

  /**
   * Evaluates the 5-step workflow progression for the active video.
   */
  readonly workflowSteps = computed<WorkflowStepInfo[]>(() => {
    const video = this.currentVideo();
    const activeJob = this.activeJobForCurrentVideo();
    const isDirty = this.chunkService.isDirty();
    const budgetSum = this.chunkService.budgetSum();
    const chunkCount = this.chunkService.chunks().length;

    if (!video) {
      return [
        { id: 'chunk', stepNumber: 1, label: 'Chunk', state: 'locked', reason: 'No video selected' },
        { id: 'extract', stepNumber: 2, label: 'Extract', state: 'locked', reason: 'No video selected' },
        { id: 'curate', stepNumber: 3, label: 'Curate', state: 'locked', reason: 'No video selected' },
        { id: 'caption', stepNumber: 4, label: 'Caption', state: 'locked', reason: 'No video selected' },
        { id: 'done', stepNumber: 5, label: 'Done', state: 'locked', reason: 'No video selected' },
      ];
    }

    const status = video.status;
    const isExtractingJob = activeJob && (activeJob.type === 'extract_750' || activeJob.type === 'extract_video');
    const isCaptioningJob = activeJob && activeJob.type === 'openrouter_caption';

    let s1: StepState = 'done';
    let s2: StepState = 'locked';
    let s3: StepState = 'locked';
    let s4: StepState = 'locked';
    let s5: StepState = 'locked';

    let r2 = '';
    let r3 = '';
    let r4 = '';
    let r5 = '';

    if (status === 'new' || chunkCount === 0) {
      s1 = 'current';
      r2 = 'Define scene chunks first';
      r3 = 'Extract frames first';
      r4 = 'Curate frames first';
      r5 = 'Not finished';
    } else if (isDirty) {
      s1 = 'current';
      r2 = 'Save chunk changes first';
      r3 = 'Extract frames first';
      r4 = 'Curate frames first';
      r5 = 'Not finished';
    } else if (budgetSum !== 100) {
      s1 = 'error';
      r2 = `Budget sum is ${budgetSum}% (must be 100%)`;
      r3 = 'Extract frames first';
      r4 = 'Curate frames first';
      r5 = 'Not finished';
    } else if (isExtractingJob || status === 'extracting' || status === 'extracting_750') {
      s1 = 'done';
      s2 = 'running';
      r3 = 'Extraction in progress...';
      r4 = 'Curate frames first';
      r5 = 'Not finished';
    } else if (status === 'chunked') {
      s1 = 'done';
      s2 = 'current';
      r3 = 'Run extraction to generate candidate frames';
      r4 = 'Curate frames first';
      r5 = 'Not finished';
    } else if (isCaptioningJob || status === 'captioning') {
      s1 = 'done';
      s2 = 'done';
      s3 = 'done';
      s4 = 'running';
      r5 = 'Captioning in progress...';
    } else if (status === 'curating' || status === 'curating_300') {
      s1 = 'done';
      s2 = 'done';
      s3 = 'current';
      const kept = this.curateService.distribution()?.selected_count ?? video.kept_count ?? 0;
      if (kept > 0) {
        s4 = 'current';
      } else {
        r4 = 'Review and keep frames before captioning';
      }
      r5 = 'Caption and export frames first';
    } else if (status === 'exported') {
      s1 = 'done';
      s2 = 'done';
      s3 = 'done';
      s4 = 'done';
      s5 = 'done';
    }

    return [
      { id: 'chunk', stepNumber: 1, label: 'Chunk', state: s1 },
      { id: 'extract', stepNumber: 2, label: 'Extract & Score', state: s2, reason: r2 },
      { id: 'curate', stepNumber: 3, label: 'Curate 300', state: s3, reason: r3 },
      { id: 'caption', stepNumber: 4, label: 'Caption VLM', state: s4, reason: r4 },
      { id: 'done', stepNumber: 5, label: 'Done', state: s5, reason: r5 },
    ];
  });

  /**
   * The single primary "Next Action" recommended for the current workspace context.
   */
  readonly nextAction = computed<NextActionInfo>(() => {
    const video = this.currentVideo();
    const activeJob = this.activeJobForCurrentVideo();
    const isDirty = this.chunkService.isDirty();
    const budgetSum = this.chunkService.budgetSum();
    const chunkCount = this.chunkService.chunks().length;

    if (!video) {
      return {
        label: 'Select a video',
        actionId: 'next_video',
        disabled: true,
        whyDisabled: 'Choose a video from the queue or board to start',
        isPrimary: false,
      };
    }

    if (video.broken) {
      return {
        label: 'Skip Broken Video',
        actionId: 'next_video',
        disabled: false,
        whyDisabled: video.broken_reason || 'File corrupted or unreadable',
        isPrimary: false,
      };
    }

    if (activeJob) {
      return {
        label: 'Next Video',
        actionId: 'next_video',
        disabled: false,
        whyDisabled: `${activeJob.step} running in background (${Math.round(activeJob.progress * 100)}%)`,
        isPrimary: false,
      };
    }

    if (chunkCount === 0 || video.status === 'new') {
      return {
        label: 'Auto-suggest Chunks',
        actionId: 'auto_suggest',
        disabled: false,
        isPrimary: true,
      };
    }

    if (isDirty) {
      return {
        label: 'Save Chunks',
        actionId: 'save_chunks',
        disabled: false,
        isPrimary: true,
      };
    }

    if (budgetSum !== 100) {
      return {
        label: 'Normalise Budgets (100%)',
        actionId: 'normalise_budget',
        disabled: false,
        whyDisabled: `Current sum is ${budgetSum}%. Must equal 100% to extract.`,
        isPrimary: true,
      };
    }

    if (video.status === 'chunked') {
      return {
        label: 'Extract & Score 750',
        actionId: 'extract',
        disabled: false,
        isPrimary: true,
      };
    }

    if (video.status === 'curating' || video.status === 'curating_300') {
      const kept = this.curateService.distribution()?.selected_count ?? video.kept_count ?? 0;
      if (kept >= 1) {
        return {
          label: `Caption ${kept} Frames`,
          actionId: 'caption',
          disabled: false,
          isPrimary: true,
        };
      }
      return {
        label: 'Curate Frames',
        actionId: 'curate',
        disabled: false,
        whyDisabled: 'Review and select frames to reach 300 candidates',
        isPrimary: true,
      };
    }

    if (video.status === 'exported') {
      return {
        label: 'Next Video',
        actionId: 'next_video',
        disabled: false,
        whyDisabled: 'Video is fully captioned and exported',
        isPrimary: false,
      };
    }

    return {
      label: 'Next Video',
      actionId: 'next_video',
      disabled: false,
      isPrimary: false,
    };
  });
}
