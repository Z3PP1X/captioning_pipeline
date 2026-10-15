import { ChangeDetectionStrategy, Component, computed, inject, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideLoader2,
  lucideLock,
  lucideAlertTriangle,
  lucideArrowRight,
  lucideScissors,
  lucideSparkles,
  lucideFilm,
  lucideClock,
  lucideCheckCircle2,
  lucideChevronRight,
  lucideListChecks,
  lucideCaptions,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmTooltipImports } from '@spartan-ng/helm/tooltip';
import { WorkflowService, WorkflowStepInfo, WorkflowStepId } from '../../core/services/workflow.service';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { CurateService } from '../../core/services/curate.service';
import { formatShortTime } from '../../core/utils/time.utils';
import { videoStatusMeta } from '../../core/ui/status';

@Component({
  selector: 'app-workflow-bar',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmTooltipImports],
  providers: [
    provideIcons({
      lucideCheck,
      lucideLoader2,
      lucideLock,
      lucideAlertTriangle,
      lucideArrowRight,
      lucideScissors,
      lucideSparkles,
      lucideFilm,
      lucideClock,
      lucideCheckCircle2,
      lucideChevronRight,
      lucideListChecks,
      lucideCaptions,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (video(); as v) {
      <div class="flex flex-col border-b border-border bg-card shadow-xs select-none z-30 shrink-0">
        <!-- Top Row: Video Identity & Stepper -->
        <div class="flex flex-wrap items-center justify-between gap-3 px-4 py-2 border-b border-border/50">
          <!-- Left: Video Info & Status -->
          <div class="flex items-center gap-3 min-w-0">
            <h2 class="text-sm font-bold text-foreground truncate max-w-[260px] md:max-w-[360px]" [title]="v.name">
              {{ v.name }}
            </h2>
            <div class="flex items-center gap-1.5 text-xs text-muted-foreground tabular-nums">
              <span>{{ formatShortTime(v.duration) }}</span>
              <span>&middot;</span>
              <span>{{ v.width }}x{{ v.height }}</span>
              @if (v.fps) {
                <span>&middot;</span>
                <span>{{ Math.round(v.fps) }}fps</span>
              }
            </div>
            <span
              hlmBadge
              [variant]="statusMeta().variant"
              size="sm"
              class="gap-1 font-semibold text-[11px]"
            >
              {{ statusMeta().label }}
            </span>

            @if (v.target_person_name) {
              <button
                type="button"
                (click)="openPersonModal.emit()"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors cursor-pointer"
                title="Target person: {{ v.target_person_name }}. Click to change."
              >
                <span>🎯 {{ v.target_person_name }}</span>
              </button>
            } @else {
              <button
                type="button"
                (click)="openPersonModal.emit()"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
                title="Assign target person for face-prioritized extraction"
              >
                <span>🎯 + Person</span>
              </button>
            }
          </div>

          <!-- Center/Right: Stepper Navigation -->
          <nav aria-label="Workflow Steps" class="flex items-center gap-1 sm:gap-2">
            @for (step of steps(); track step.id; let idx = $index; let last = $last) {
              <button
                type="button"
                [disabled]="step.state === 'locked'"
                (click)="onStepClick(step)"
                [attr.aria-current]="step.state === 'current' ? 'step' : null"
                [attr.aria-label]="step.label + ': ' + step.state + (step.reason ? ' - ' + step.reason : '')"
                class="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-semibold transition-colors disabled:cursor-not-allowed"
                [class.text-primary]="step.state === 'current'"
                [class.bg-primary/10]="step.state === 'current'"
                [class.text-success]="step.state === 'done'"
                [class.text-warning]="step.state === 'running'"
                [class.text-destructive]="step.state === 'error'"
                [class.text-muted-foreground]="step.state === 'locked'"
                [class.opacity-60]="step.state === 'locked'"
                [class.hover:bg-muted]="step.state !== 'locked' && step.state !== 'current'"
                [title]="step.reason || (step.state === 'done' ? 'Completed' : step.label)"
              >
                <!-- Step State Icon -->
                @switch (step.state) {
                  @case ('done') {
                    <span class="flex size-4 items-center justify-center rounded-full bg-success/20 text-success">
                      <ng-icon name="lucideCheck" class="size-2.5 font-bold" />
                    </span>
                  }
                  @case ('running') {
                    <span class="flex size-4 items-center justify-center rounded-full bg-warning/20 text-warning animate-spin">
                      <ng-icon name="lucideLoader2" class="size-2.5" />
                    </span>
                  }
                  @case ('error') {
                    <span class="flex size-4 items-center justify-center rounded-full bg-destructive/20 text-destructive">
                      <ng-icon name="lucideAlertTriangle" class="size-2.5" />
                    </span>
                  }
                  @case ('locked') {
                    <span class="flex size-4 items-center justify-center rounded-full bg-muted text-muted-foreground">
                      <ng-icon name="lucideLock" class="size-2.5" />
                    </span>
                  }
                  @default {
                    <span class="flex size-4 items-center justify-center rounded-full bg-primary text-primary-foreground font-black text-[10px]">
                      {{ step.stepNumber }}
                    </span>
                  }
                }
                <span class="hidden sm:inline">{{ step.label }}</span>
              </button>

              @if (!last) {
                <span class="text-muted-foreground/40 text-xs select-none" aria-hidden="true">&rarr;</span>
              }
            }
          </nav>
        </div>

        <!-- Bottom Row: Context Summary & Sticky Next Action Bar -->
        <div class="flex items-center justify-between gap-4 px-4 py-1.5 bg-muted/40">
          <!-- Left: Contextual State Summary -->
          <div class="flex items-center gap-3 text-xs">
            @if (activeJob(); as job) {
              <div class="flex items-center gap-2 text-warning font-medium">
                <ng-icon name="lucideLoader2" class="size-3.5 animate-spin" />
                <span>{{ job.type }}: {{ Math.round(job.progress * 100) }}% ({{ job.step || 'Running' }})</span>
              </div>
            } @else if (v.status === 'curating' || v.status === 'curating_300') {
              <div class="flex items-center gap-2">
                <span class="font-semibold text-foreground">
                  Kept: <strong class="text-primary">{{ curateStats().kept }}</strong>/300
                </span>
                <span class="text-muted-foreground">&middot;</span>
                <span class="text-muted-foreground">
                  {{ curateStats().total }} candidates available
                </span>
              </div>
            } @else if (v.status === 'chunked') {
              <div class="flex items-center gap-2">
                <span class="font-medium text-foreground">
                  {{ chunkService.chunks().length }} Chunks
                </span>
                <span class="text-muted-foreground">&middot;</span>
                <span [class.text-destructive]="chunkService.budgetSum() !== 100" [class.text-success]="chunkService.budgetSum() === 100">
                  Budget: {{ chunkService.budgetSum() }}%
                </span>
                @if (chunkService.isDirty()) {
                  <span hlmBadge variant="warning" size="sm" class="text-[10px] py-0 px-1.5">Unsaved Changes</span>
                }
              </div>
            } @else if (v.status === 'exported') {
              <div class="flex items-center gap-1.5 text-success font-medium">
                <ng-icon name="lucideCheckCircle2" class="size-3.5" />
                <span>Video dataset exports complete</span>
              </div>
            } @else {
              <span class="text-muted-foreground">
                Set chunk boundaries and budgets to begin extraction.
              </span>
            }
          </div>

          <!-- Right: Why-Disabled Context & Primary Action Button -->
          <div class="flex items-center gap-2">
            @if (nextAction().whyDisabled) {
              <span class="text-xs text-muted-foreground italic hidden md:inline">
                {{ nextAction().whyDisabled }}
              </span>
            }

            <button
              hlmBtn
              [variant]="nextAction().isPrimary ? 'default' : 'secondary'"
              size="sm"
              [disabled]="nextAction().disabled || isActionBusy()"
              (click)="onExecuteNextAction()"
              class="cursor-pointer gap-1.5 font-bold shadow-xs transition-all"
            >
              @if (isActionBusy()) {
                <ng-icon name="lucideLoader2" class="size-3.5 animate-spin" />
              } @else {
                <ng-icon name="lucideArrowRight" class="size-3.5" />
              }
              <span>{{ nextAction().label }}</span>
            </button>
          </div>
        </div>
      </div>
    }
  `,
})
export class WorkflowBarComponent {
  private readonly workflowService = inject(WorkflowService);
  private readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);
  private readonly curateService = inject(CurateService);

  readonly tabChange = output<'studio' | 'curate' | 'dataset'>();
  readonly openCaptionModal = output<void>();
  readonly openPersonModal = output<void>();

  readonly Math = Math;

  readonly video = this.workflowService.currentVideo;
  readonly activeJob = this.workflowService.activeJobForCurrentVideo;
  readonly steps = this.workflowService.workflowSteps;
  readonly nextAction = this.workflowService.nextAction;

  readonly statusMeta = computed(() => {
    const v = this.video();
    if (!v) {
      return { variant: 'muted' as const, icon: 'lucideCircleDashed', label: 'No video' };
    }
    return videoStatusMeta(v, this.activeJob());
  });

  readonly curateStats = computed(() => {
    const dist = this.curateService.distribution();
    const v = this.video();
    const candCount = this.curateService.candidates().length;
    return {
      kept: dist?.selected_count ?? v?.kept_count ?? 0,
      total: candCount > 0 ? candCount : (dist?.target_count ?? 300),
    };
  });

  readonly isActionBusy = this.chunkService.isExtracting;

  onStepClick(step: WorkflowStepInfo): void {
    if (step.state === 'locked') return;

    switch (step.id) {
      case 'chunk':
      case 'extract':
        this.tabChange.emit('studio');
        break;
      case 'curate':
        this.tabChange.emit('curate');
        break;
      case 'caption':
        this.openCaptionModal.emit();
        break;
      case 'done':
        this.tabChange.emit('dataset');
        break;
    }
  }

  async onExecuteNextAction(): Promise<void> {
    const action = this.nextAction();
    const v = this.video();
    if (!v || action.disabled) return;

    switch (action.actionId) {
      case 'auto_suggest':
        await this.chunkService.autoSuggest(v.id);
        break;
      case 'save_chunks':
        await this.chunkService.saveChunks(v.id);
        break;
      case 'normalise_budget':
        this.chunkService.normalizeBudgets();
        await this.chunkService.saveChunks(v.id);
        break;
      case 'extract':
        await this.chunkService.extract750(v.id);
        break;
      case 'curate':
        this.tabChange.emit('curate');
        break;
      case 'caption':
        this.openCaptionModal.emit();
        break;
      case 'next_video':
        await this.videoService.selectNextVideo();
        break;
      case 'restore':
        await this.videoService.updateStatus(v.id, 'new');
        break;
    }
  }

  readonly formatShortTime = formatShortTime;
}
