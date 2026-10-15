import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideRefreshCw,
  lucideSearch,
  lucideDownload,
  lucideClock,
  lucideMaximize2,
  lucideFilm,
  lucidePackage,
  lucideAlertTriangle,
  lucideLoader2,
  lucideScissors,
  lucideCircleDashed,
  lucideListChecks,
  lucideCaptions,
  lucideCheck,
  lucideSkipForward,
  lucideBan,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { formatShortTime } from '../../core/utils/time.utils';
import { Video } from '../../core/models/video.model';
import { ActiveJob } from '../../core/models/job.model';
import { videoStatusMeta, ALL_VIDEO_STATUSES, StatusMeta } from '../../core/ui/status';

@Component({
  selector: 'app-video-queue',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideRefreshCw,
      lucideSearch,
      lucideDownload,
      lucideClock,
      lucideMaximize2,
      lucideFilm,
      lucidePackage,
      lucideAlertTriangle,
      lucideLoader2,
      lucideScissors,
      lucideCircleDashed,
      lucideListChecks,
      lucideCaptions,
      lucideCheck,
      lucideSkipForward,
      lucideBan,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="flex w-72 md:w-80 flex-col border-r border-border bg-card shrink-0 h-full overflow-hidden select-none">
      <!-- Queue Header -->
      <div class="flex flex-col gap-2 p-3 border-b border-border bg-card">
        <div class="flex items-center justify-between">
          <h2 class="font-sans font-bold text-sm tracking-wide text-foreground uppercase">
            Video Queue
          </h2>
          <div class="flex items-center gap-1">
            <button
              hlmBtn
              variant="ghost"
              size="icon-xs"
              (click)="onSyncCaptions()"
              [disabled]="videoService.isLoading()"
              title="Sync captions with finished folder"
              class="cursor-pointer"
            >
              <ng-icon name="lucideDownload" class="size-3.5" />
              <span class="sr-only">Sync Captions</span>
            </button>
            <button
              hlmBtn
              variant="ghost"
              size="icon-xs"
              (click)="onRescan()"
              [disabled]="videoService.isLoading()"
              title="Rescan video library directory"
              class="cursor-pointer"
            >
              <ng-icon
                name="lucideRefreshCw"
                class="size-3.5"
                [class.animate-spin]="videoService.isLoading()"
              />
              <span class="sr-only">Rescan</span>
            </button>
          </div>
        </div>

        <!-- Search Bar -->
        <div class="relative flex items-center">
          <ng-icon name="lucideSearch" class="absolute left-2.5 size-3.5 text-muted-foreground pointer-events-none" />
          <input
            type="text"
            [value]="videoService.searchQuery()"
            (input)="onSearchChange($any($event.target).value)"
            placeholder="Search videos..."
            autocomplete="off"
            class="h-8 w-full rounded-md border border-input bg-background pl-8 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>

        <!-- Status Filter & Counter -->
        <div class="flex items-center justify-between gap-2">
          <select
            [value]="videoService.statusFilter()"
            (change)="onStatusChange($any($event.target).value)"
            class="h-7 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">All Statuses</option>
            @for (s of statuses; track s.value) {
              <option [value]="s.value">{{ s.label }}</option>
            }
          </select>

          <span class="text-xs font-medium text-muted-foreground">
            {{ videoService.filteredVideos().length }} videos
          </span>
        </div>
      </div>

      <!-- Video Scroll List -->
      <div class="flex-1 overflow-y-auto p-2 space-y-1.5">
        @for (video of videoService.filteredVideos(); track video.id) {
          @let activeJob = getActiveJob(video.id);
          @let isSelected = videoService.currentVideo()?.id === video.id;
          @let meta = getStatusMeta(video, activeJob);

          <div
            (click)="selectVideo(video.id)"
            (keydown.enter)="selectVideo(video.id)"
            (keydown.space)="selectVideo(video.id); $event.preventDefault()"
            role="button"
            tabindex="0"
            [attr.aria-selected]="isSelected"
            class="group relative flex flex-col gap-1 rounded-lg border p-2.5 transition-all cursor-pointer text-xs focus:outline-none focus:ring-2 focus:ring-ring"
            [class.border-primary]="isSelected"
            [class.bg-muted]="isSelected"
            [class.hover:bg-muted/60]="!isSelected"
            [class.border-border]="!isSelected"
            [class.opacity-60]="video.broken"
          >
            <!-- Card Top: Name & Status Badge -->
            <div class="flex items-start justify-between gap-2">
              <span
                class="font-medium text-foreground truncate"
                [title]="video.name"
              >
                {{ video.name }}
              </span>
              <span
                hlmBadge
                [variant]="meta.variant"
                class="shrink-0 text-[11px] gap-1 font-medium"
              >
                <ng-icon
                  [name]="meta.icon"
                  class="size-3"
                  [class.motion-safe:animate-spin]="meta.spin"
                />
                {{ meta.label }}
              </span>
            </div>

            <!-- Card Meta: Duration, Dimensions, Chunks, Exports, Kept -->
            <div class="flex items-center flex-wrap gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
              <span class="inline-flex items-center gap-1">
                <ng-icon name="lucideClock" class="size-3 text-muted-foreground" />
                {{ formatShortTime(video.duration) }}
              </span>
              <span class="inline-flex items-center gap-1">
                <ng-icon name="lucideMaximize2" class="size-3 text-muted-foreground" />
                {{ video.width }}x{{ video.height }}
              </span>
              <span class="inline-flex items-center gap-1">
                <ng-icon name="lucideFilm" class="size-3 text-muted-foreground" />
                {{ video.chunk_count || 0 }}c
              </span>
              @if ((video.kept_count || 0) > 0) {
                <span class="inline-flex items-center gap-1 text-foreground font-medium">
                  <ng-icon name="lucideListChecks" class="size-3 text-success" />
                  {{ video.kept_count }}/300
                </span>
              }
              <span class="inline-flex items-center gap-1">
                <ng-icon name="lucidePackage" class="size-3 text-muted-foreground" />
                {{ video.export_count || 0 }}e
              </span>
            </div>

            <!-- Extraction Progress Bar (if active job running) -->
            @if (activeJob) {
              <div class="mt-1 flex flex-col gap-0.5">
                <div class="h-1.5 w-full rounded-full bg-border overflow-hidden">
                  <div
                    class="h-full bg-primary transition-all duration-300"
                    [style.width.%]="activeJob.progress * 100"
                  ></div>
                </div>
                <div class="flex justify-between text-xs text-primary">
                  <span>{{ activeJob.step }}</span>
                  <span class="tabular-nums">{{ Math.round(activeJob.progress * 100) }}%</span>
                </div>
              </div>
            }
          </div>
        } @empty {
          <div class="flex flex-col items-center justify-center p-6 text-center text-xs text-muted-foreground">
            No videos found matching current filters.
          </div>
        }
      </div>
    </aside>
  `,
})
export class VideoQueueComponent {
  readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);

  readonly formatShortTime = formatShortTime;
  readonly Math = Math;
  readonly statuses = ALL_VIDEO_STATUSES;

  getActiveJob(videoId: string | number) {
    for (const job of this.jobEvents.activeJobs().values()) {
      if (String(job.video_id) === String(videoId)) return job;
    }
    return null;
  }

  onRescan(): void {
    this.videoService.rescan();
  }

  onSyncCaptions(): void {
    this.videoService.syncCaptions();
  }

  onSearchChange(query: string): void {
    this.videoService.searchQuery.set(query);
  }

  onStatusChange(status: string): void {
    this.videoService.statusFilter.set(status);
  }

  selectVideo(id: string): void {
    this.videoService.selectVideo(id);
  }

  getStatusMeta(video: Video, activeJob: ActiveJob | null): StatusMeta {
    return videoStatusMeta(video, activeJob);
  }
}
