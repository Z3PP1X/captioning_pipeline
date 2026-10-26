import { ChangeDetectionStrategy, Component, computed, inject, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideSearch,
  lucideRefreshCw,
  lucideDownload,
  lucideFilm,
  lucideClock,
  lucidePackage,
  lucideListChecks,
  lucideZap,
  lucideCheck,
  lucideScissors,
  lucideAlertTriangle,
  lucideArrowRight,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { DatasetService } from '../../core/services/dataset.service';
import { formatShortTime } from '../../core/utils/time.utils';
import { Video } from '../../core/models/video.model';
import { videoStatusMeta, ALL_VIDEO_STATUSES, StatusMeta } from '../../core/ui/status';

export interface BoardNavigateEvent {
  tab: 'studio' | 'curate';
  videoId: string;
}

@Component({
  selector: 'app-pipeline-board',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideSearch,
      lucideRefreshCw,
      lucideDownload,
      lucideFilm,
      lucideClock,
      lucidePackage,
      lucideListChecks,
      lucideZap,
      lucideCheck,
      lucideScissors,
      lucideAlertTriangle,
      lucideArrowRight,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex-1 flex flex-col h-full w-full overflow-hidden bg-canvas p-4 md:p-6 select-none">
      <!-- Top Title & Toolbar -->
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 class="font-display font-black text-xl md:text-2xl uppercase tracking-wide text-foreground">
            Pipeline board
          </h1>
          <p class="text-xs text-muted-foreground mt-0.5">
            {{ allVideos().length }} videos in library · {{ runningCount() }} active jobs · {{ needsActionCount() }} awaiting curation
          </p>
        </div>

        <div class="flex items-center gap-2">
          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="onBatchAutoExtract()"
            [disabled]="isBatchRunning() || videoService.isLoading()"
            class="cursor-pointer gap-1.5 font-semibold"
            title="Sequentially auto-chunk (if needed) and extract frames for all unextracted videos (suitable for overnight runs)"
          >
            <ng-icon
              [name]="isBatchRunning() ? 'lucideRefreshCw' : 'lucideZap'"
              class="size-3.5"
              [class.motion-safe:animate-spin]="isBatchRunning()"
            />
            <span>{{ isBatchRunning() ? 'Batch Running...' : 'Auto-Extract All (Overnight)' }}</span>
          </button>

          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="onScan()"
            [disabled]="videoService.isLoading()"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon
              name="lucideRefreshCw"
              class="size-3.5"
              [class.motion-safe:animate-spin]="videoService.isLoading()"
            />
            <span>Rescan Library</span>
          </button>

          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="onExportAll()"
            [disabled]="videoService.isLoading()"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideDownload" class="size-3.5" />
            <span>Export Curated</span>
          </button>
        </div>
      </div>

      <!-- Batch Auto-Extract Active Progress Banner -->
      @if (batchJob()) {
        <div class="mt-4 flex flex-col gap-2 rounded-xl border border-warning bg-warning/10 p-3.5 text-xs" role="status">
          <div class="flex items-center justify-between font-bold text-foreground">
            <div class="flex items-center gap-2">
              <ng-icon name="lucideRefreshCw" class="size-4 text-foreground motion-safe:animate-spin" aria-hidden="true" />
              <span>Overnight batch auto-extraction: {{ batchJob()!.step || 'Processing' }}</span>
            </div>
            <span class="text-sm tabular-nums">{{ (batchJob()!.progress * 100).toFixed(0) }}%</span>
          </div>
          <div class="h-2 w-full rounded-full bg-border overflow-hidden">
            <div
              class="h-full bg-warning transition-all duration-300 rounded-full"
              [style.width.%]="batchJob()!.progress * 100"
            ></div>
          </div>
        </div>
      }

      <!-- Filters & Search Bar -->
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3 py-3">
        <!-- Stage Filter Chips -->
        <div class="flex flex-wrap items-center gap-1.5 text-xs">
          <button
            (click)="filterStage.set('all')"
            class="rounded-full px-2.5 py-1 font-medium transition-colors cursor-pointer border"
            [class]="filterStage() === 'all' ? 'bg-primary text-primary-foreground border-primary' : 'bg-card text-foreground border-border hover:bg-muted'"
          >
            All ({{ allVideos().length }})
          </button>

          @for (stage of stageFilters; track stage.id) {
            @let count = getCountForStage(stage.id);
            <button
              (click)="filterStage.set(stage.id)"
              class="rounded-full px-2.5 py-1 font-medium transition-colors cursor-pointer border"
              [class]="filterStage() === stage.id ? 'bg-primary text-primary-foreground border-primary' : 'bg-card text-foreground border-border hover:bg-muted'"
            >
              {{ stage.label }} ({{ count }})
            </button>
          }
        </div>

        <!-- Search Bar -->
        <div class="relative w-full lg:w-72">
          <ng-icon
            name="lucideSearch"
            class="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground pointer-events-none"
          />
          <input
            type="text"
            [value]="searchQuery()"
            (input)="searchQuery.set($any($event.target).value)"
            placeholder="Search videos by name or slug..."
            class="h-8 w-full rounded-md border border-input bg-card pl-8 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      <!-- Table Container -->
      <div class="flex-1 overflow-auto rounded-xl border border-border bg-card shadow-xs">
        <table class="w-full text-left text-xs border-collapse">
          <thead class="bg-muted/50 sticky top-0 border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-xs">
            <tr>
              <th class="py-2.5 px-4">Video</th>
              <th class="py-2.5 px-3">Stage Progression</th>
              <th class="py-2.5 px-3 text-center">Chunks</th>
              <th class="py-2.5 px-3 text-center">Kept / 300</th>
              <th class="py-2.5 px-3 text-center">Exported</th>
              <th class="py-2.5 px-3 text-right">Duration</th>
              <th class="py-2.5 px-4 text-right">Action</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            @for (v of displayedVideos(); track v.id) {
              @let activeJob = getActiveJob(v.id);
              @let stageMeta = getStageProgress(v, activeJob);

              <tr
                (click)="onRowClick(v)"
                class="hover:bg-muted/40 transition-colors cursor-pointer group"
              >
                <!-- Video Name & Dimensions -->
                <td class="py-2.5 px-4 font-medium text-foreground">
                  <div class="flex flex-col">
                    <span class="font-bold truncate max-w-xs md:max-w-sm group-hover:text-primary transition-colors">
                      {{ v.name }}
                    </span>
                    <span class="text-xs text-muted-foreground tabular-nums">
                      {{ v.width }}×{{ v.height }} &middot; {{ v.fps.toFixed(1) }} fps
                    </span>
                  </div>
                </td>

                <!-- Stage Progression Indicator -->
                <td class="py-2.5 px-3">
                  <div class="flex items-center gap-2">
                    @if (stageMeta.done !== null) {
                      <span class="flex items-center gap-1" aria-hidden="true">
                        @for (i of stageSlots; track i) {
                          <span
                            class="size-1.5 rounded-full"
                            [class]="i < stageMeta.done ? 'bg-primary' : i === stageMeta.done && stageMeta.running ? 'bg-primary/40' : 'bg-border'"
                          ></span>
                        }
                      </span>
                    }
                    <span
                      hlmBadge
                      [variant]="stageMeta.variant"
                      class="text-xs px-1.5 py-0 font-medium shrink-0"
                    >
                      {{ stageMeta.label }}
                    </span>
                  </div>
                </td>

                <!-- Chunks -->
                <td class="py-2.5 px-3 text-center tabular-nums text-foreground">
                  {{ v.chunk_count || 0 }}
                </td>

                <!-- Kept / 300 -->
                <td class="py-2.5 px-3 text-center tabular-nums">
                  @if ((v.kept_count || 0) > 0) {
                    <span class="font-bold text-success-strong">{{ v.kept_count }}</span><span class="text-muted-foreground">/300</span>
                  } @else {
                    <span class="text-muted-foreground">&mdash;</span>
                  }
                </td>

                <!-- Exported -->
                <td class="py-2.5 px-3 text-center tabular-nums">
                  @if ((v.export_count || 0) > 0) {
                    <span class="font-bold text-foreground">{{ v.export_count }}</span>
                  } @else {
                    <span class="text-muted-foreground">&mdash;</span>
                  }
                </td>

                <!-- Duration -->
                <td class="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {{ formatShortTime(v.duration) }}
                </td>

                <!-- Next Action CTA Button -->
                <td class="py-2.5 px-4 text-right" (click)="$event.stopPropagation()">
                  <div class="flex items-center justify-end gap-1.5">
                    @if ((v.status === 'new' || v.status === 'chunked') && !activeJob) {
                      <button
                        hlmBtn
                        variant="outline"
                        size="xs"
                        (click)="onAutoExtractVideo(v)"
                        title="Auto-extract candidate frames for this video"
                        class="cursor-pointer gap-1"
                      >
                        <ng-icon name="lucideZap" class="size-3" />
                        <span class="hidden sm:inline">Auto</span>
                      </button>
                    }
                    <button
                      hlmBtn
                      variant="outline"
                      size="xs"
                      (click)="onAction(v, stageMeta.targetTab)"
                      class="cursor-pointer gap-1 font-semibold group-hover:border-primary group-hover:text-primary transition-colors"
                    >
                      <span>{{ stageMeta.actionLabel }}</span>
                      <ng-icon name="lucideArrowRight" class="size-3" />
                    </button>
                  </div>
                </td>
              </tr>
            } @empty {
              <tr>
                <td colspan="7" class="py-12 text-center text-xs text-muted-foreground">
                  No videos match your active filter.
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </div>
  `,
})
export class PipelineBoardComponent {
  readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);
  private readonly datasetService = inject(DatasetService);

  readonly navigate = output<BoardNavigateEvent>();

  readonly filterStage = signal<string>('all');
  readonly searchQuery = signal<string>('');

  readonly allVideos = this.videoService.videos;

  readonly stageFilters = [
    { id: 'new', label: 'New' },
    { id: 'chunked', label: 'Chunked' },
    { id: 'extracting', label: 'Extracting' },
    { id: 'curating', label: 'Curating' },
    { id: 'captioning', label: 'Captioning' },
    { id: 'exported', label: 'Exported' },
    { id: 'skipped', label: 'Skipped' },
  ];

  readonly runningCount = computed(() => this.jobEvents.activeJobs().size);

  readonly batchJob = computed(() => {
    for (const job of this.jobEvents.activeJobs().values()) {
      if (job.type === 'batch_auto_extract') {
        return job;
      }
    }
    return null;
  });

  readonly isBatchRunning = computed(() => !!this.batchJob());

  readonly needsActionCount = computed(() => {
    return this.allVideos().filter((v) => v.status === 'curating' || v.status === 'curating_300').length;
  });

  getCountForStage(stage: string): number {
    const list = this.allVideos();
    if (stage === 'curating') {
      return list.filter((v) => v.status === 'curating' || v.status === 'curating_300').length;
    }
    if (stage === 'extracting') {
      return list.filter((v) => v.status === 'extracting' || v.status === 'extracting_750').length;
    }
    return list.filter((v) => v.status === stage).length;
  }

  readonly displayedVideos = computed(() => {
    let list = this.allVideos();
    const stage = this.filterStage();
    const q = this.searchQuery().trim().toLowerCase();

    if (stage !== 'all') {
      if (stage === 'curating') {
        list = list.filter((v) => v.status === 'curating' || v.status === 'curating_300');
      } else if (stage === 'extracting') {
        list = list.filter((v) => v.status === 'extracting' || v.status === 'extracting_750');
      } else {
        list = list.filter((v) => v.status === stage);
      }
    }

    if (q) {
      list = list.filter((v) => v.name.toLowerCase().includes(q) || v.slug.toLowerCase().includes(q));
    }

    return list;
  });

  getActiveJob(videoId: string) {
    for (const job of this.jobEvents.activeJobs().values()) {
      if (String(job.video_id) === String(videoId)) {
        return job;
      }
    }
    return null;
  }

  /** Five pipeline stages (chunk, extract, curate, caption, export) shown as dots next to the badge. */
  readonly stageSlots = [0, 1, 2, 3, 4];

  private readonly staticStageMap: Record<
    string,
    {
      done: number | null;
      running?: boolean;
      label: string;
      variant: 'default' | 'outline' | 'secondary' | 'destructive' | 'warning' | 'success';
      actionLabel: string;
      targetTab: 'studio' | 'curate';
    }
  > = {
    new: { done: 0, label: 'New', variant: 'outline', actionLabel: 'Chunk', targetTab: 'studio' },
    chunked: { done: 1, label: 'Chunked', variant: 'secondary', actionLabel: 'Extract', targetTab: 'studio' },
    extracting: { done: 2, running: true, label: 'Extracting', variant: 'warning', actionLabel: 'Inspect', targetTab: 'studio' },
    extracting_750: { done: 2, running: true, label: 'Extracting', variant: 'warning', actionLabel: 'Inspect', targetTab: 'studio' },
    captioning: { done: 4, running: true, label: 'Captioning', variant: 'warning', actionLabel: 'Monitor', targetTab: 'curate' },
    exported: { done: 5, label: 'Exported', variant: 'success', actionLabel: 'View Set', targetTab: 'curate' },
    skipped: { done: null, label: 'Skipped', variant: 'outline', actionLabel: 'Restore', targetTab: 'studio' },
  };

  getStageProgress(v: Video, activeJob: any): {
    /** Completed stages (0-5); null = no progression (broken / skipped). */
    done: number | null;
    /** The stage at index `done` is in progress. */
    running?: boolean;
    label: string;
    variant: 'default' | 'outline' | 'secondary' | 'destructive' | 'warning' | 'success';
    actionLabel: string;
    targetTab: 'studio' | 'curate';
  } {
    if (v.broken) {
      return { done: null, label: 'Broken', variant: 'destructive', actionLabel: 'Review', targetTab: 'studio' };
    }
    if (activeJob) {
      const pct = Math.round(activeJob.progress * 100);
      return { done: 2, running: true, label: `${activeJob.step} ${pct}%`, variant: 'warning', actionLabel: 'View', targetTab: 'studio' };
    }
    if (v.status === 'curating' || v.status === 'curating_300') {
      return {
        done: 3,
        label: `Curating (${v.kept_count || 0}/300)`,
        variant: 'default',
        actionLabel: 'Curate',
        targetTab: 'curate',
      };
    }
    return this.staticStageMap[v.status] ?? { done: 0, label: v.status, variant: 'outline', actionLabel: 'Open', targetTab: 'studio' };
  }

  onRowClick(video: Video): void {
    this.videoService.selectVideo(video.id);
    const meta = this.getStageProgress(video, this.getActiveJob(video.id));
    this.navigate.emit({ tab: meta.targetTab, videoId: video.id });
  }

  onAction(video: Video, targetTab: 'studio' | 'curate'): void {
    this.videoService.selectVideo(video.id);
    this.navigate.emit({ tab: targetTab, videoId: video.id });
  }

  async onScan(): Promise<void> {
    await this.videoService.rescan();
  }

  async onExportAll(): Promise<void> {
    await this.datasetService.exportAllCurated();
  }

  async onBatchAutoExtract(): Promise<void> {
    try {
      await this.videoService.batchAutoExtract(undefined, true);
      this.jobEvents.showToast(
        'Overnight Batch Dispatched',
        'Auto-extracting all unextracted videos sequentially...',
        null
      );
    } catch (err) {
      this.jobEvents.showToast('Batch Dispatch Failed', String(err), null, true);
    }
  }

  async onAutoExtractVideo(video: Video): Promise<void> {
    try {
      await this.videoService.autoExtractSingle(video.id);
      this.jobEvents.showToast(
        'Auto-Extraction Dispatched',
        `Extracting candidate frames for ${video.name}...`,
        video.id
      );
    } catch (err) {
      this.jobEvents.showToast('Auto-Extraction Failed', String(err), video.id, true);
    }
  }

  readonly formatShortTime = formatShortTime;
}
