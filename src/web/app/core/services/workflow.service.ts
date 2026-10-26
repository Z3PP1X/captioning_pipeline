import { Injectable, computed, inject, signal } from '@angular/core';
import { VideoService } from './video.service';
import { ChunkService } from './chunk.service';
import { CurateService } from './curate.service';
import { JobEventService } from './job-event.service';
import { Video } from '../models/video.model';
import { ActiveJob } from '../models/job.model';

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
    const ctx = this.readContext();

    if (!video) {
      return [
        { id: 'chunk', stepNumber: 1, label: 'Chunk', state: 'locked', reason: 'No video selected' },
        { id: 'extract', stepNumber: 2, label: 'Extract', state: 'locked', reason: 'No video selected' },
        { id: 'curate', stepNumber: 3, label: 'Curate', state: 'locked', reason: 'No video selected' },
        { id: 'caption', stepNumber: 4, label: 'Caption', state: 'locked', reason: 'No video selected' },
        { id: 'done', stepNumber: 5, label: 'Done', state: 'locked', reason: 'No video selected' },
      ];
    }

    const plan = planChunkPhase(video.status, ctx) ?? this.planProcessingPhase(video, ctx.activeJob);
    const [s1, s2, s3, s4, s5] = plan.states;
    const [r2, r3, r4, r5] = plan.reasons;

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
    const ctx = this.readContext();

    if (!video) {
      return {
        label: 'Select a video',
        actionId: 'next_video',
        disabled: true,
        whyDisabled: 'Choose a video from the queue or board to start',
        isPrimary: false,
      };
    }

    return blockingAction(video, ctx.activeJob) ?? chunkAction(video, ctx) ?? this.statusAction(video);
  });

  /** Reads every signal the workflow computeds depend on (kept unconditional for stable dependency tracking). */
  private readContext(): WorkflowContext {
    return {
      activeJob: this.activeJobForCurrentVideo(),
      isDirty: this.chunkService.isDirty(),
      budgetSum: this.chunkService.budgetSum(),
      chunkCount: this.chunkService.chunks().length,
    };
  }

  private keptCount(video: Video): number {
    return this.curateService.distribution()?.selected_count ?? video.kept_count ?? 0;
  }

  /** Steps once chunks are saved and budgeted: extraction, curation, captioning, export. */
  private planProcessingPhase(video: Video, activeJob: ActiveJob | null): StepPlan {
    const status = video.status;
    if (isExtractingPhase(status, activeJob)) {
      return plan(['done', 'running', 'locked', 'locked', 'locked'], ['', 'Extraction in progress...', CURATE_FIRST, NOT_FINISHED]);
    }
    if (status === 'chunked') {
      return plan(
        ['done', 'current', 'locked', 'locked', 'locked'],
        ['', 'Run extraction to generate candidate frames', CURATE_FIRST, NOT_FINISHED]
      );
    }
    if (isCaptioningPhase(status, activeJob)) {
      return plan(['done', 'done', 'done', 'running', 'locked'], ['', '', '', 'Captioning in progress...']);
    }
    if (status === 'curating' || status === 'curating_300') {
      const kept = this.keptCount(video);
      return plan(
        ['done', 'done', 'current', kept > 0 ? 'current' : 'locked', 'locked'],
        ['', '', kept > 0 ? '' : 'Review and keep frames before captioning', 'Caption and export frames first']
      );
    }
    if (status === 'exported') {
      return plan(['done', 'done', 'done', 'done', 'done'], ['', '', '', '']);
    }
    return plan(['done', 'locked', 'locked', 'locked', 'locked'], ['', '', '', '']);
  }

  /** Next action derived from the video status once chunks are saved and budgeted. */
  private statusAction(video: Video): NextActionInfo {
    if (video.status === 'chunked') {
      return {
        label: 'Extract & Score 750',
        actionId: 'extract',
        disabled: false,
        isPrimary: true,
      };
    }

    if (video.status === 'curating' || video.status === 'curating_300') {
      const kept = this.keptCount(video);
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
  }
}

interface WorkflowContext {
  activeJob: ActiveJob | null;
  isDirty: boolean;
  budgetSum: number;
  chunkCount: number;
}

/** States of steps 1-5 and reasons of steps 2-5. */
interface StepPlan {
  states: [StepState, StepState, StepState, StepState, StepState];
  reasons: [string, string, string, string];
}

const EXTRACT_FIRST = 'Extract frames first';
const CURATE_FIRST = 'Curate frames first';
const NOT_FINISHED = 'Not finished';

function plan(states: StepPlan['states'], reasons: StepPlan['reasons']): StepPlan {
  return { states, reasons };
}

function isExtractingPhase(status: string, activeJob: ActiveJob | null): boolean {
  const isExtractingJob = !!activeJob && (activeJob.type === 'extract_750' || activeJob.type === 'extract_video');
  return isExtractingJob || status === 'extracting' || status === 'extracting_750';
}

function isCaptioningPhase(status: string, activeJob: ActiveJob | null): boolean {
  return activeJob?.type === 'openrouter_caption' || status === 'captioning';
}

/** Steps while the chunk definition is still incomplete (none, unsaved, or budgets not at 100%). */
function planChunkPhase(status: string, ctx: WorkflowContext): StepPlan | null {
  if (status === 'new' || ctx.chunkCount === 0) {
    return plan(
      ['current', 'locked', 'locked', 'locked', 'locked'],
      ['Define scene chunks first', EXTRACT_FIRST, CURATE_FIRST, NOT_FINISHED]
    );
  }
  if (ctx.isDirty) {
    return plan(
      ['current', 'locked', 'locked', 'locked', 'locked'],
      ['Save chunk changes first', EXTRACT_FIRST, CURATE_FIRST, NOT_FINISHED]
    );
  }
  if (ctx.budgetSum !== 100) {
    return plan(
      ['error', 'locked', 'locked', 'locked', 'locked'],
      [`Budget sum is ${ctx.budgetSum}% (must be 100%)`, EXTRACT_FIRST, CURATE_FIRST, NOT_FINISHED]
    );
  }
  return null;
}

/** Broken files and running jobs take precedence over any editing action. */
function blockingAction(video: Video, activeJob: ActiveJob | null): NextActionInfo | null {
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
  return null;
}

/** Chunk editing actions: suggest, save, normalise budgets. */
function chunkAction(video: Video, ctx: WorkflowContext): NextActionInfo | null {
  if (ctx.chunkCount === 0 || video.status === 'new') {
    return {
      label: 'Auto-suggest Chunks',
      actionId: 'auto_suggest',
      disabled: false,
      isPrimary: true,
    };
  }

  if (ctx.isDirty) {
    return {
      label: 'Save Chunks',
      actionId: 'save_chunks',
      disabled: false,
      isPrimary: true,
    };
  }

  if (ctx.budgetSum !== 100) {
    return {
      label: 'Normalise Budgets (100%)',
      actionId: 'normalise_budget',
      disabled: false,
      whyDisabled: `Current sum is ${ctx.budgetSum}%. Must equal 100% to extract.`,
      isPrimary: true,
    };
  }
  return null;
}
