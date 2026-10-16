import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideZap,
  lucideSettings,
  lucideSkipForward,
  lucideX,
  lucideFilm,
  lucideSearch,
  lucidePackage,
  lucideSparkles,
  lucideLayoutDashboard,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmTooltipImports } from '@spartan-ng/helm/tooltip';
import { JobEventService } from '../../core/services/job-event.service';
import { VideoService } from '../../core/services/video.service';
import { ActiveJob } from '../../core/models/job.model';

@Component({
  selector: 'app-header',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmTooltipImports],
  providers: [
    provideIcons({
      lucideZap,
      lucideSettings,
      lucideSkipForward,
      lucideX,
      lucideFilm,
      lucideSearch,
      lucidePackage,
      lucideSparkles,
      lucideLayoutDashboard,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="flex h-14 md:h-16 w-full items-center justify-between border-b border-border bg-card px-4 md:px-6 z-40 select-none">
      <!-- Left: Logo & Nav Tabs -->
      <div class="flex items-center gap-6">
        <div class="flex items-center gap-2 text-foreground">
          <span class="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground font-black shadow-xs">
            <ng-icon name="lucideZap" class="size-4" />
          </span>
          <span class="font-display font-black text-lg md:text-xl tracking-wide uppercase">
            Captioning Pipeline
          </span>
        </div>

        <nav class="flex items-center gap-1 rounded-lg bg-muted p-1" aria-label="Main Navigation">
          <button
            hlmBtn
            [variant]="currentTab() === 'board' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('board')"
            class="transition-all cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideLayoutDashboard" class="size-3.5" />
            <span>Board</span>
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'studio' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('studio')"
            class="transition-all cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideFilm" class="size-3.5" />
            <span>Chunking Studio</span>
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'curate' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('curate')"
            class="transition-all cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideSearch" class="size-3.5" />
            <span>Curation Grid</span>
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'dataset' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('dataset')"
            class="transition-all cursor-pointer gap-1.5"
          >
            <ng-icon name="lucidePackage" class="size-3.5" />
            <span>Finished Dataset</span>
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'prompt-clicker' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('prompt-clicker')"
            class="transition-all cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideSparkles" class="size-3.5" />
            <span>Prompt Director</span>
          </button>
        </nav>
      </div>

      <!-- Right: Active Jobs Ticker, Next Video, Settings & Theme Toggle -->
      <div class="flex items-center gap-2 md:gap-3">
        <!-- Multi-Job Progress Pills & SSE State -->
        <div class="flex items-center gap-2 max-w-sm md:max-w-md overflow-x-auto py-1">
          <!-- Live SSE Connection Indicator -->
          <div
            class="flex items-center gap-1.5 px-2 py-1 rounded-full text-xs text-muted-foreground bg-muted/60 border border-border/60 shrink-0"
            [title]="'SSE Event Stream: ' + connectionState()"
            [attr.aria-label]="'SSE Event Stream: ' + connectionState()"
          >
            <span
              class="size-2 rounded-full"
              [class.bg-success-strong]="connectionState() === 'connected'"
              [class.bg-warning-strong]="connectionState() === 'connecting'"
              [class.animate-pulse]="connectionState() === 'connecting'"
              [class.bg-destructive]="connectionState() === 'disconnected'"
            ></span>
            <span class="text-xs font-semibold hidden xl:inline uppercase tracking-wider">{{ connectionState() }}</span>
          </div>

          @for (job of activeJobsList(); track job.job_id) {
            <div
              (click)="onJobClick(job)"
              (keydown.enter)="onJobClick(job)"
              (keydown.space)="onJobClick(job); $event.preventDefault()"
              role="button"
              tabindex="0"
              class="flex items-center gap-2 rounded-full border border-border bg-muted/80 hover:bg-muted px-2.5 py-1 text-xs cursor-pointer transition-all shadow-xs focus:outline-none focus:ring-1 focus:ring-ring"
              [title]="'Job #' + job.job_id + ' on ' + job.video_name + '. Click to inspect.'"
              [attr.aria-label]="'Job on ' + job.video_name + ': ' + job.step + ', ' + Math.round(job.progress * 100) + '% complete. Click to inspect'"
            >
              <ng-icon name="lucideZap" class="size-3.5 text-primary" />
              <div class="flex flex-col min-w-0">
                <span class="truncate max-w-[100px] font-medium text-foreground">{{ job.video_name }}</span>
                <span class="text-xs text-muted-foreground tabular-nums">{{ job.step }} ({{ Math.round(job.progress * 100) }}%)</span>
              </div>
              <div class="w-12 h-1.5 rounded-full bg-border overflow-hidden">
                <div class="h-full bg-primary transition-all duration-300" [style.width.%]="job.progress * 100"></div>
              </div>
              <button
                (click)="onCancelJob($event, job.job_id)"
                class="hover:text-destructive text-muted-foreground p-0.5 cursor-pointer"
                [title]="'Cancel job for ' + job.video_name"
                [attr.aria-label]="'Cancel job for ' + job.video_name"
              >
                <ng-icon name="lucideX" class="size-3" />
              </button>
            </div>
          }
        </div>

        <!-- Next Video CTA -->
        <button
          hlmBtn
          variant="outline"
          size="sm"
          (click)="nextVideoClick.emit()"
          title="Jump to next unprocessed video (Hotkey: N)"
          class="hidden sm:inline-flex cursor-pointer gap-1.5"
        >
          <ng-icon name="lucideSkipForward" class="size-4" />
          <span>Next Video</span>
          <kbd class="ml-1 rounded-md border border-border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">N</kbd>
        </button>

        <!-- Pipeline Settings Trigger -->
        <button
          hlmBtn
          variant="ghost"
          size="icon"
          (click)="openSettingsClick.emit()"
          title="Global Pipeline Settings"
          class="cursor-pointer"
        >
          <ng-icon name="lucideSettings" class="size-4 text-foreground" />
          <span class="sr-only">Settings</span>
        </button>
      </div>
    </header>
  `,
})
export class HeaderComponent {
  private readonly jobEvents = inject(JobEventService);
  private readonly videoService = inject(VideoService);

  readonly connectionState = this.jobEvents.connectionState;
  readonly currentTab = input<'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker'>('board');
  readonly tabChange = output<'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker'>();
  readonly nextVideoClick = output<void>();
  readonly openSettingsClick = output<void>();

  readonly Math = Math;

  readonly activeJobsList = computed<ActiveJob[]>(() => {
    return Array.from(this.jobEvents.activeJobs().values());
  });

  onJobClick(job: ActiveJob): void {
    this.videoService.selectVideo(job.video_id);
    this.tabChange.emit('curate');
  }

  onCancelJob(event: MouseEvent, jobId: number): void {
    event.stopPropagation();
    this.jobEvents.cancelJob(jobId);
  }
}
