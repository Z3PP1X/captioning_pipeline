import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideRefreshCw,
  lucidePackage,
  lucideChevronDown,
  lucideChevronUp,
  lucideQuote,
  lucideScanSearch,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { DatasetService } from '../../core/services/dataset.service';
import { PromptRagService } from '../../core/services/prompt-rag.service';
import { JobEventService } from '../../core/services/job-event.service';
import { CandidateFrame } from '../../core/models/candidate.model';
import { formatTime } from '../../core/utils/time.utils';

@Component({
  selector: 'app-dataset-view',
  imports: [NgIcon, HlmButtonImports],
  providers: [
    provideIcons({ lucideRefreshCw, lucidePackage, lucideChevronDown, lucideChevronUp, lucideQuote, lucideScanSearch }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-6 p-4 md:p-6 overflow-y-auto h-full">
      <!-- Dataset Header -->
      <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 shadow-xs select-none">
        <div>
          <h1 class="font-display font-black text-lg md:text-xl uppercase tracking-wide text-foreground">
            Finished dataset
          </h1>
          <span class="text-xs text-muted-foreground">
            Directory: <code class="font-sans bg-muted px-1.5 py-0.5 rounded text-xs break-all">{{ datasetService.metrics().finished_dir }}</code>
          </span>
        </div>

        <div class="flex items-center gap-2">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="datasetService.loadDataset()"
            [disabled]="datasetService.isLoading()"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon
              name="lucideRefreshCw"
              class="size-3.5"
              [class.animate-spin]="datasetService.isLoading()"
            />
            <span>Refresh</span>
          </button>

          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="datasetService.exportAllCurated()"
            [disabled]="datasetService.isLoading()"
            class="cursor-pointer gap-1.5 font-bold"
          >
            <ng-icon name="lucidePackage" class="size-3.5" />
            <span>Export All Curated Videos</span>
          </button>
        </div>
      </div>

      <!-- KPI Metrics Cards (Signature KPI Typography) -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <!-- Card 1: Total Images -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-3xl md:text-4xl text-primary leading-none">
            {{ datasetService.metrics().total_exports }}
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Total Exported Images
          </span>
        </div>

        <!-- Card 2: Curated Videos -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-3xl md:text-4xl text-foreground leading-none">
            {{ datasetService.metrics().per_video.length }}
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Curated Videos
          </span>
        </div>

        <!-- Card 3: Image Spec -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-2xl md:text-3xl text-foreground leading-none">
            1MP Buckets
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Max 4 Buckets · Center-Crop (1344×768 / 1152×896 / 896×1152 / 768×1344)
          </span>
        </div>
      </div>

      <!-- Exported Breakdown Table -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-xs">
        <div class="p-4 border-b border-border">
          <h3 class="font-sans font-bold text-sm text-foreground">
            Exported Videos Breakdown
          </h3>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs">
            <thead class="bg-muted text-muted-foreground font-semibold uppercase tracking-wider text-xs border-b border-border">
              <tr>
                <th class="p-3">Video Name</th>
                <th class="p-3 text-right">Frames Exported</th>
                <th class="p-3">Per-Video Folder</th>
                <th class="p-3">Action</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-border">
              @for (item of datasetService.metrics().per_video; track item.id) {
                <tr class="hover:bg-muted/40 transition-colors">
                  <td class="p-3 font-medium text-foreground">{{ item.name }}</td>
                  <td class="p-3 tabular-nums text-primary font-bold text-right">{{ item.count }}</td>
                  <td class="p-3 text-muted-foreground text-xs break-all">
                    /finished/{{ item.slug }}/
                  </td>
                  <td class="p-3">
                    <div class="flex flex-wrap items-center gap-2">
                      <button
                        hlmBtn
                        variant="outline"
                        size="xs"
                        (click)="datasetService.exportVideo(item.id)"
                        class="cursor-pointer"
                      >
                        Re-Export
                      </button>
                      <button
                        hlmBtn
                        variant="ghost"
                        size="xs"
                        type="button"
                        (click)="toggleFrames(item.id)"
                        [attr.aria-expanded]="isExpanded(item.id)"
                        [attr.aria-controls]="'ds-frames-' + item.id"
                      >
                        <ng-icon [name]="isExpanded(item.id) ? 'lucideChevronUp' : 'lucideChevronDown'" class="size-3.5" aria-hidden="true" />
                        Frames
                      </button>
                    </div>
                  </td>
                </tr>
                @if (isExpanded(item.id)) {
                  @let st = datasetService.exportedFrames()['' + item.id];
                  <tr [id]="'ds-frames-' + item.id">
                    <td colspan="4" class="bg-muted/40 p-3">
                      @if (!st || (st.loading && st.frames.length === 0)) {
                        <p class="flex items-center gap-1 text-xs text-muted-foreground">
                          <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" aria-hidden="true" /> Loading exported frames...
                        </p>
                      } @else if (st.error) {
                        <p class="text-xs font-medium text-destructive" role="alert">Could not load frames: {{ st.error }}</p>
                      } @else if (st.frames.length === 0) {
                        <p class="text-xs text-muted-foreground">
                          No exported frame of this video is in the current curation set (it may have been re-extracted).
                        </p>
                      } @else {
                        <p class="pb-2 text-xs text-muted-foreground">
                          Use a frame's caption as the Prompt Director description, or find similar exemplars.
                        </p>
                        <ul class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                          @for (f of st.frames; track f.id) {
                            <li class="flex flex-col gap-1 rounded-lg border border-border bg-card p-2">
                              <img
                                [src]="f.preview_url"
                                [alt]="'Exported frame at ' + formatTime(f.time_s)"
                                class="aspect-video w-full rounded-md bg-secondary object-cover"
                                loading="lazy"
                              />
                              <span class="text-xs tabular-nums text-muted-foreground">{{ formatTime(f.time_s) }}</span>
                              <div class="flex items-center gap-1">
                                <button
                                  hlmBtn
                                  variant="outline"
                                  size="sm"
                                  type="button"
                                  class="flex-1"
                                  (click)="onUseAsSeed(item.name, f)"
                                  [disabled]="rag.isSeeding()"
                                  [attr.aria-label]="'Use frame at ' + formatTime(f.time_s) + ' as prompt seed'"
                                >
                                  <ng-icon name="lucideQuote" class="size-3" aria-hidden="true" />
                                  Seed
                                </button>
                                <button
                                  hlmBtn
                                  variant="ghost"
                                  size="icon-sm"
                                  type="button"
                                  (click)="onMoreLikeThis(item.name, f)"
                                  [attr.aria-label]="'More like the frame at ' + formatTime(f.time_s)"
                                  title="More like this frame"
                                >
                                  <ng-icon name="lucideScanSearch" class="size-3.5" aria-hidden="true" />
                                </button>
                              </div>
                            </li>
                          }
                        </ul>
                      }
                    </td>
                  </tr>
                }
              } @empty {
                <tr>
                  <td colspan="4" class="p-6 text-center text-muted-foreground">
                    No datasets exported yet. Curate candidate frames and click Export.
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
})
export class DatasetViewComponent {
  readonly datasetService = inject(DatasetService);
  readonly rag = inject(PromptRagService);
  private readonly jobEvents = inject(JobEventService);
  readonly formatTime = formatTime;

  /** Video ids whose exported-frames row is open. */
  private readonly expanded = signal<ReadonlySet<string>>(new Set());

  isExpanded(videoId: string | number): boolean {
    return this.expanded().has(String(videoId));
  }

  toggleFrames(videoId: string | number): void {
    const key = String(videoId);
    const open = !this.isExpanded(key);
    this.expanded.update((s) => {
      const next = new Set(s);
      if (open) next.add(key);
      else next.delete(key);
      return next;
    });
    if (open) void this.datasetService.loadExportedFrames(key);
  }

  async onUseAsSeed(videoName: string, f: CandidateFrame): Promise<void> {
    const error = await this.rag.useFrameAsSeed(f.id, `${videoName} · ${formatTime(f.time_s)}`);
    if (error) this.jobEvents.showToast('No prompt seed', error, null, true);
  }

  onMoreLikeThis(videoName: string, f: CandidateFrame): void {
    this.rag.showSimilarTo(f.id, `${videoName} · ${formatTime(f.time_s)}`);
  }
}
