import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideZap,
  lucideSettings,
  lucideSkipForward,
  lucideX,
  lucideMoon,
  lucideSun,
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
      lucideMoon,
      lucideSun,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="flex h-14 md:h-16 w-full items-center justify-between border-b border-border bg-card px-4 md:px-6 z-40 select-none">
      <!-- Left: Logo & Nav Tabs -->
      <div class="flex items-center gap-6">
        <div class="flex items-center gap-2 text-foreground">
          <span class="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground font-black shadow-xs">
            ⚡
          </span>
          <span class="font-display font-black text-lg md:text-xl tracking-wide uppercase">
            Captioning Pipeline
          </span>
        </div>

        <nav class="flex items-center gap-1 rounded-lg bg-muted p-1" aria-label="Main Navigation">
          <button
            hlmBtn
            [variant]="currentTab() === 'studio' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('studio')"
            class="transition-all cursor-pointer"
          >
            🎬 Chunking Studio
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'curate' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('curate')"
            class="transition-all cursor-pointer"
          >
            🔍 Curation Grid
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'dataset' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('dataset')"
            class="transition-all cursor-pointer"
          >
            📦 Finished Dataset
          </button>
          <button
            hlmBtn
            [variant]="currentTab() === 'prompt-clicker' ? 'default' : 'ghost'"
            size="sm"
            (click)="tabChange.emit('prompt-clicker')"
            class="transition-all cursor-pointer"
          >
            🎯 Prompt Director
          </button>
        </nav>
      </div>

      <!-- Right: Active Jobs Ticker, Next Video, Settings & Theme Toggle -->
      <div class="flex items-center gap-2 md:gap-3">
        <!-- Multi-Job Progress Pills -->
        <div class="flex items-center gap-2 max-w-sm md:max-w-md overflow-x-auto py-1">
          @for (job of activeJobsList(); track job.job_id) {
            <div
              (click)="onJobClick(job)"
              class="flex items-center gap-2 rounded-full border border-border bg-muted/80 hover:bg-muted px-2.5 py-1 text-xs cursor-pointer transition-all shadow-xs"
              [title]="'Job #' + job.job_id + ' on ' + job.video_name + '. Click to inspect.'"
            >
              <span class="text-primary animate-pulse font-bold">⚡</span>
              <div class="flex flex-col min-w-0">
                <span class="truncate max-w-[100px] font-medium text-foreground">{{ job.video_name }}</span>
                <span class="text-[10px] text-muted-foreground">{{ job.step }} ({{ Math.round(job.progress * 100) }}%)</span>
              </div>
              <div class="w-12 h-1.5 rounded-full bg-border overflow-hidden">
                <div class="h-full bg-primary transition-all duration-300" [style.width.%]="job.progress * 100"></div>
              </div>
              <button
                (click)="onCancelJob($event, job.job_id)"
                class="hover:text-destructive text-muted-foreground p-0.5"
                title="Cancel extraction job"
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
          <kbd class="ml-1 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">N</kbd>
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

        <!-- Light/Dark Mode Toggle -->
        <button
          hlmBtn
          variant="ghost"
          size="icon"
          (click)="toggleTheme()"
          title="Toggle Dark/Light Mode"
          class="cursor-pointer"
        >
          @if (isDark()) {
            <ng-icon name="lucideSun" class="size-4 text-warning" />
          } @else {
            <ng-icon name="lucideMoon" class="size-4 text-foreground" />
          }
          <span class="sr-only">Toggle theme</span>
        </button>
      </div>
    </header>
  `,
})
export class HeaderComponent {
  private readonly jobEvents = inject(JobEventService);
  private readonly videoService = inject(VideoService);

  readonly currentTab = input<'studio' | 'curate' | 'dataset' | 'prompt-clicker'>('studio');
  readonly tabChange = output<'studio' | 'curate' | 'dataset' | 'prompt-clicker'>();
  readonly nextVideoClick = output<void>();
  readonly openSettingsClick = output<void>();

  readonly Math = Math;

  readonly isDark = computed(() => {
    return document.documentElement.classList.contains('dark');
  });

  readonly activeJobsList = computed<ActiveJob[]>(() => {
    return Array.from(this.jobEvents.activeJobs().values());
  });

  toggleTheme(): void {
    const root = document.documentElement;
    root.classList.toggle('dark');
  }

  onJobClick(job: ActiveJob): void {
    this.videoService.selectVideo(job.video_id);
    this.tabChange.emit('curate');
  }

  onCancelJob(event: MouseEvent, jobId: number): void {
    event.stopPropagation();
    this.jobEvents.cancelJob(jobId);
  }
}
