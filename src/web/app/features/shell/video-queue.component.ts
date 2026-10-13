import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideRefreshCw, lucideSearch } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { formatShortTime } from '../../core/utils/time.utils';
import { Video } from '../../core/models/video.model';
import { ActiveJob } from '../../core/models/job.model';

@Component({
  selector: 'app-video-queue',
  imports: [FormsModule, NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [provideIcons({ lucideRefreshCw, lucideSearch })],
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
              <span class="text-xs" title="Sync captions from final folder">📥</span>
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
            [ngModel]="videoService.searchQuery()"
            (ngModelChange)="onSearchChange($event)"
            placeholder="Search videos..."
            autocomplete="off"
            class="h-8 w-full rounded-md border border-input bg-background pl-8 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>

        <!-- Status Filter & Counter -->
        <div class="flex items-center justify-between gap-2">
          <select
            [ngModel]="videoService.statusFilter()"
            (ngModelChange)="onStatusChange($event)"
            class="h-7 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">All Statuses</option>
            <option value="new">New</option>
            <option value="chunked">Chunked</option>
            <option value="extracting">Extracting</option>
            <option value="curating">Curating</option>
            <option value="captioning">Captioning</option>
            <option value="exported">Exported</option>
            <option value="skipped">Skipped</option>
          </select>

          <span class="text-[11px] font-medium text-muted-foreground">
            {{ videoService.filteredVideos().length }} videos
          </span>
        </div>
      </div>

      <!-- Video Scroll List -->
      <div class="flex-1 overflow-y-auto p-2 space-y-1.5">
        @for (video of videoService.filteredVideos(); track video.id) {
          @let activeJob = getActiveJob(video.id);
          @let isSelected = videoService.currentVideo()?.id === video.id;

          <div
            (click)="selectVideo(video.id)"
            class="group relative flex flex-col gap-1 rounded-lg border p-2.5 transition-all cursor-pointer text-xs"
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
                class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
                [class]="getStatusBadgeClass(video, activeJob)"
              >
                {{ getStatusText(video, activeJob) }}
              </span>
            </div>

            <!-- Card Meta: Duration, Dimensions, Chunks, Exports -->
            <div class="flex items-center gap-2.5 text-[11px] text-muted-foreground">
              <span>⏱️ {{ formatShortTime(video.duration) }}</span>
              <span>📐 {{ video.width }}x{{ video.height }}</span>
              <span>🎞️ {{ video.chunk_count || 0 }}c</span>
              <span>📦 {{ video.export_count || 0 }}e</span>
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
                <div class="flex justify-between text-[10px] text-primary">
                  <span>{{ activeJob.step }}</span>
                  <span>{{ Math.round(activeJob.progress * 100) }}%</span>
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

  getStatusBadgeClass(video: Video, activeJob: ActiveJob | null): string {
    if (video.broken) return 'bg-destructive/10 text-destructive border border-destructive/20';
    if (activeJob) {
      if (activeJob.type === 'openrouter_caption' || video.status === 'captioning') {
        return 'bg-primary/20 text-primary border border-primary/30 animate-pulse';
      }
      return 'bg-amber-500/20 text-amber-500 border border-amber-500/30';
    }
    switch (video.status) {
      case 'chunked':
        return 'bg-warning/15 text-warning border border-warning/20';
      case 'curating':
      case 'curating_300':
        return 'bg-info/15 text-info border border-info/20';
      case 'captioning':
        return 'bg-primary/15 text-primary border border-primary/20 animate-pulse';
      case 'exported':
        return 'bg-success/15 text-success border border-success/20';
      default:
        return 'bg-muted text-muted-foreground border border-border';
    }
  }

  getStatusText(video: Video, activeJob: ActiveJob | null): string {
    if (video.broken) return video.broken_reason || 'BROKEN';
    if (activeJob) {
      if (activeJob.type === 'openrouter_caption' || video.status === 'captioning') {
        return 'CAPTIONING';
      }
      return 'EXTRACTING';
    }
    if (video.status === 'curating_300') return 'CURATING';
    return (video.status || 'NEW').toUpperCase();
  }
}
