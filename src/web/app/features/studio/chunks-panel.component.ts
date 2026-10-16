import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideTrash2,
  lucideSparkles,
  lucideSave,
  lucideZap,
  lucidePlay,
  lucideRefreshCw,
  lucideCheck,
  lucideAlertTriangle,
  lucideAlertCircle,
  lucideClock,
  lucideChevronDown,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmDropdownMenuImports } from '@spartan-ng/helm/dropdown-menu';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { ApiError } from '../../core/services/api.service';
import { ChunkService, MAX_CHUNKS } from '../../core/services/chunk.service';
import { ConfirmService } from '../../core/services/confirm.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { SettingsService } from '../../core/services/settings.service';
import { formatHms, parseTimestamp } from '../../core/utils/time.utils';
import { ChunkItem } from '../../core/models/chunk.model';

@Component({
  selector: 'app-chunks-panel',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmDropdownMenuImports, HlmNativeSelectImports],
  providers: [
    provideIcons({
      lucideTrash2,
      lucideSparkles,
      lucideSave,
      lucideZap,
      lucidePlay,
      lucideRefreshCw,
      lucideCheck,
      lucideAlertTriangle,
      lucideAlertCircle,
      lucideClock,
      lucideChevronDown,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-xs select-none">
      <!-- Chunks Header -->
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <div class="flex items-center gap-2">
          <h3 class="font-sans font-bold text-base text-foreground">
            Defined Scene Chunks
          </h3>
          <span class="rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
            {{ chunkService.chunks().length }} / {{ maxChunks }} max
          </span>
          <span
            hlmBadge
            [variant]="budgetVariant()"
            class="gap-1 font-medium text-xs"
          >
            <ng-icon [name]="budgetIcon()" class="size-3" />
            Budget: {{ chunkService.budgetSum() }}%{{ chunkService.budgetSum() !== 100 ? ' (must be 100%)' : '' }}
          </span>
          @if (chunkService.isDirty()) {
            <span hlmBadge variant="warning" class="gap-1 font-medium text-xs">
              <ng-icon name="lucideAlertCircle" class="size-3" />
              Unsaved changes
            </span>
          }
        </div>

        <div class="flex items-center gap-2">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            class="cursor-pointer gap-1.5"
            [hlmDropdownMenuTrigger]="bulkModelMenu"
            [disabled]="!chunkService.chunks().length || !settingsService.openRouterModels().length"
          >
            <span>Set all models</span>
            <ng-icon name="lucideChevronDown" class="size-3.5" aria-hidden="true" />
          </button>
          <ng-template #bulkModelMenu>
            <hlm-dropdown-menu class="min-w-56">
              <hlm-dropdown-menu-label>Caption model for every chunk</hlm-dropdown-menu-label>
              @for (m of settingsService.openRouterModels(); track m.id) {
                <button hlmDropdownMenuItem (triggered)="onBulkModelChange(m.id)">{{ m.name }}</button>
              }
            </hlm-dropdown-menu>
          </ng-template>

          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="onAutoSuggest()"
            [disabled]="!videoService.currentVideo()"
            title="Auto-create up to 15 chunks using detected scene cuts"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideSparkles" class="size-3.5 text-primary" />
            <span>Auto-Suggest (Max 15)</span>
          </button>

          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="onSyncCaptions()"
            [disabled]="videoService.isLoading()"
            title="Check which scenes are already fully captioned from disk"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideRefreshCw" class="size-3.5" [class.animate-spin]="videoService.isLoading()" />
            <span>Sync Captions</span>
          </button>

          <button
            hlmBtn
            variant="secondary"
            size="sm"
            (click)="onSaveChunks()"
            [disabled]="!videoService.currentVideo()"
            title="Save chunk boundaries & budget weights"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideSave" class="size-3.5" />
            <span>Save Chunks</span>
          </button>

          <div class="flex flex-col items-end">
            <button
              hlmBtn
              variant="default"
              size="sm"
              (click)="onExtract750()"
              [disabled]="!videoService.currentVideo() || chunkService.chunks().length === 0 || chunkService.isExtracting() || !!activeExtractionJob()"
              class="cursor-pointer gap-1.5 font-bold"
            >
              @if (activeExtractionJob()) {
                <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" />
                <span>Extracting ({{ (activeExtractionJob()!.progress * 100).toFixed(0) }}%)...</span>
              } @else if (chunkService.isExtracting()) {
                <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" />
                <span>Starting...</span>
              } @else {
                <ng-icon name="lucideZap" class="size-3.5" />
                <span>Extract & Score 750</span>
              }
            </button>
            @if (disabledReason()) {
              <span class="text-xs text-muted-foreground mt-0.5">
                {{ disabledReason() }}
              </span>
            }
          </div>
        </div>
      </div>

      <!-- Active Extraction Progress Banner -->
      @if (activeExtractionJob()) {
        <div class="flex flex-col gap-1.5 rounded-lg border border-primary/30 bg-primary/10 p-3 text-xs">
          <div class="flex items-center justify-between font-semibold text-primary">
            <div class="flex items-center gap-2">
              <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" />
              <span>Extracting & Scoring 750 Candidates: {{ activeExtractionJob()!.step || 'Processing' }}</span>
            </div>
            <span class="font-bold tabular-nums">{{ (activeExtractionJob()!.progress * 100).toFixed(0) }}%</span>
          </div>
          <div class="h-2 w-full rounded-full bg-border overflow-hidden">
            <div
              class="h-full bg-primary transition-all duration-300 rounded-full"
              [style.width.%]="activeExtractionJob()!.progress * 100"
            ></div>
          </div>
        </div>
      }

      <!-- Vertically Aligned Chunks List -->
      <div class="flex flex-col gap-1.5 max-h-80 overflow-y-auto overflow-x-auto pr-1">
        <!-- List Column Headers -->
        @if (chunkService.chunks().length > 0) {
          <div class="grid grid-cols-[135px_95px_95px_85px_1fr_110px_165px_72px] items-center gap-2.5 px-3 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/50 min-w-[880px]">
            <div>Chunk</div>
            <div>Start</div>
            <div>End</div>
            <div>Duration</div>
            <div>Frame Budget</div>
            <div>Rule</div>
            <div>Model</div>
            <div class="text-right">Actions</div>
          </div>
        }

        @for (chunk of chunkService.chunks(); track chunk.position; let idx = $index) {
          <div
            (click)="seek.emit(chunk.start_s)"
            class="grid grid-cols-[135px_95px_95px_85px_1fr_110px_165px_72px] items-center gap-2.5 px-3 py-2 rounded-lg border transition-all text-xs cursor-pointer min-w-[880px]"
            [class]="isCurrentChunk(chunk)
              ? 'border-primary bg-primary/10 shadow-xs ring-1 ring-primary/40'
              : 'border-border bg-background hover:border-primary/50 hover:bg-muted/30'"
            title="Click to jump to {{ formatHms(chunk.start_s) }}"
          >
            <!-- Column 1: Chunk Number & Jump Play Icon & Captioned Badge -->
            <div class="flex items-center gap-1.5 min-w-0">
              <button
                type="button"
                (click)="seek.emit(chunk.start_s); $event.stopPropagation()"
                class="size-6 shrink-0 rounded flex items-center justify-center text-primary hover:bg-primary/20 transition-colors"
                title="Jump to start of chunk"
              >
                <ng-icon name="lucidePlay" class="size-3.5 fill-primary/30" />
              </button>

              <span class="font-bold text-foreground truncate">
                Chunk #{{ chunk.position + 1 }}
              </span>

              @if (chunk.captioned_count !== undefined && (chunk.picks_count || 0) > 0) {
                @if (chunk.is_fully_captioned) {
                  <span
                    hlmBadge
                    variant="success"
                    class="shrink-0 text-xs px-1.5 py-0 h-4.5 gap-1"
                    title="Captioned"
                  >
                    <ng-icon name="lucideCheck" class="size-2.5" />
                  </span>
                } @else {
                  <span
                    hlmBadge
                    variant="outline"
                    class="shrink-0 text-xs px-1.5 py-0 h-4.5 font-medium gap-1"
                    title="Progress"
                  >
                    <ng-icon name="lucideClock" class="size-2.5" />
                    {{ chunk.captioned_count }}/{{ chunk.picks_count }}
                  </span>
                }
              }
            </div>

            <!-- Column 2: Start Time (hh:mm:ss) -->
            <div class="flex items-center" (click)="$event.stopPropagation()">
              <input
                type="text"
                [value]="formatHms(chunk.start_s)"
                (change)="onStartTimeChange(idx, $any($event.target).value, chunk)"
                (keydown.enter)="$any($event.target).blur()"
                title="Start time (hh:mm:ss or seconds)"
                class="h-7 w-21 rounded border border-input bg-card px-2 text-center tabular-nums text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <!-- Column 3: End Time (hh:mm:ss) -->
            <div class="flex items-center" (click)="$event.stopPropagation()">
              <input
                type="text"
                [value]="formatHms(chunk.end_s)"
                (change)="onEndTimeChange(idx, $any($event.target).value, chunk)"
                (keydown.enter)="$any($event.target).blur()"
                title="End time (hh:mm:ss or seconds)"
                class="h-7 w-21 rounded border border-input bg-card px-2 text-center tabular-nums text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <!-- Column 4: Duration (hh:mm:ss format) -->
            <div class="flex items-center tabular-nums text-xs text-muted-foreground">
              <span class="px-2 py-0.5 rounded bg-muted/60 tabular-nums">
                {{ formatHms(chunk.end_s - chunk.start_s) }}
              </span>
            </div>

            <!-- Column 5: Frame Budget Slider & Value -->
            <div class="flex items-center gap-2 pr-2" (click)="$event.stopPropagation()">
              <input
                type="range"
                min="0"
                max="100"
                step="1"
                [value]="chunk.budget_pct"
                (input)="onBudgetChange(idx, +$any($event.target).value)"
                class="flex-1 accent-primary h-1.5 bg-muted rounded-lg cursor-pointer min-w-16"
              />
              <span class="font-bold text-primary text-xs w-8 text-right tabular-nums">
                {{ chunk.budget_pct }}%
              </span>
            </div>

            <!-- Column 6: Rule Selector -->
            <div (click)="$event.stopPropagation()">
              <label class="sr-only" [for]="'chunk-rule-' + idx">Selection rule for chunk {{ chunk.position + 1 }}</label>
              <hlm-native-select
                size="sm"
                class="w-full"
                selectClass="text-xs"
                [selectId]="'chunk-rule-' + idx"
                [value]="chunk.rule"
                (valueChange)="onRuleChange(idx, $event || 'person')"
              >
                <option value="person">Person</option>
                <option value="equipment">Equipment</option>
                <option value="both">Both</option>
                <option value="either">Either</option>
                <option value="none">None (Quality)</option>
              </hlm-native-select>
            </div>

            <!-- Column 7: Model Selector -->
            <div (click)="$event.stopPropagation()">
              <label class="sr-only" [for]="'chunk-model-' + idx">Caption model for chunk {{ chunk.position + 1 }}</label>
              <hlm-native-select
                size="sm"
                class="w-full"
                selectClass="text-xs truncate"
                [selectId]="'chunk-model-' + idx"
                [value]="chunk.caption_model || 'google/gemma-4-31b-it'"
                (valueChange)="onModelChange(idx, $event || 'google/gemma-4-31b-it')"
              >
                @for (m of settingsService.openRouterModels(); track m.id) {
                  <option [value]="m.id">{{ m.name }}</option>
                }
              </hlm-native-select>
            </div>

            <!-- Column 8: Actions (Extract Scene & Delete) -->
            <div class="flex items-center justify-end gap-1.5" (click)="$event.stopPropagation()">
              <button
                type="button"
                (click)="onExtractSingleChunk(chunk)"
                [disabled]="!chunk.id || chunkService.isExtractingChunk(chunk.id)"
                class="text-foreground hover:text-primary hover:bg-muted p-1 rounded cursor-pointer transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                title="Extract frames for this chunk only (multi-step extraction)"
                [attr.aria-label]="'Extract frames for chunk ' + (chunk.position + 1) + ' only'"
              >
                @if (chunkService.isExtractingChunk(chunk.id)) {
                  <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" />
                } @else {
                  <ng-icon name="lucideZap" class="size-3.5" />
                }
              </button>
              <button
                type="button"
                (click)="chunkService.deleteChunk(idx)"
                class="text-muted-foreground hover:text-destructive p-1 rounded cursor-pointer transition-colors"
                title="Remove this chunk"
                [attr.aria-label]="'Remove chunk ' + (chunk.position + 1)"
              >
                <ng-icon name="lucideTrash2" class="size-3.5" />
              </button>
            </div>
          </div>
        } @empty {
          <div class="col-span-full py-8 text-center text-xs text-muted-foreground">
            No scene chunks defined yet. Mark [ In and ] Out points on the video or click Auto-Suggest.
          </div>
        }
      </div>
    </div>
  `,
})
export class ChunksPanelComponent {
  readonly chunkService = inject(ChunkService);
  readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);
  readonly settingsService = inject(SettingsService);
  private readonly confirm = inject(ConfirmService);

  readonly maxChunks = MAX_CHUNKS;
  readonly currentTime = input<number>(0);
  readonly seek = output<number>();

  readonly formatHms = formatHms;

  readonly activeExtractionJob = computed(() => {
    const video = this.videoService.currentVideo();
    if (!video) return null;
    for (const job of this.jobEvents.activeJobs().values()) {
      if (
        String(job.video_id) === String(video.id) &&
        (job.type === 'extract_750' || job.type === 'extract_video')
      ) {
        return job;
      }
    }
    return null;
  });

  readonly budgetVariant = computed(() => {
    const sum = this.chunkService.budgetSum();
    if (sum === 100) return 'success';
    if (sum > 100) return 'danger';
    return 'warning';
  });

  readonly budgetIcon = computed(() => {
    const sum = this.chunkService.budgetSum();
    if (sum === 100) return 'lucideCheck';
    if (sum > 100) return 'lucideAlertTriangle';
    return 'lucideAlertCircle';
  });

  readonly disabledReason = computed(() => {
    if (!this.videoService.currentVideo()) return 'Select a video from the queue first';
    if (this.activeExtractionJob()) return `Extraction job #${this.activeExtractionJob()!.job_id} running`;
    if (this.chunkService.isExtracting()) return 'Extraction job dispatching...';
    if (this.chunkService.chunks().length === 0) return 'Auto-suggest or add scene chunks first';
    return null;
  });

  isCurrentChunk(chunk: ChunkItem): boolean {
    const cur = this.currentTime();
    return cur >= chunk.start_s && cur <= chunk.end_s;
  }

  onStartTimeChange(idx: number, rawVal: string, chunk: ChunkItem): void {
    const parsed = parseTimestamp(rawVal);
    if (parsed !== null && parsed < chunk.end_s && parsed >= 0) {
      this.chunkService.updateChunk(idx, {
        start_s: parseFloat(parsed.toFixed(2)),
      });
    } else {
      this.chunkService.updateChunk(idx, { start_s: chunk.start_s });
    }
  }

  onEndTimeChange(idx: number, rawVal: string, chunk: ChunkItem): void {
    const parsed = parseTimestamp(rawVal);
    if (parsed !== null && parsed > chunk.start_s) {
      this.chunkService.updateChunk(idx, {
        end_s: parseFloat(parsed.toFixed(2)),
      });
    } else {
      this.chunkService.updateChunk(idx, { end_s: chunk.end_s });
    }
  }

  onBudgetChange(idx: number, val: number): void {
    this.chunkService.updateChunk(idx, { budget_pct: Number(val) });
  }

  onRuleChange(idx: number, rule: string): void {
    this.chunkService.updateChunk(idx, { rule });
  }

  onModelChange(idx: number, model: string): void {
    this.chunkService.updateChunk(idx, { caption_model: model });
  }

  onBulkModelChange(model: string): void {
    if (model) {
      this.chunkService.setAllModels(model);
    }
  }

  /**
   * Runs a chunk write; guards re-chunking after curation/export or on 409 conflicts with the shared
   * alert-dialog (ConfirmService) instead of window.confirm (B4).
   */
  private async withRechunkConfirm(write: (confirm: boolean) => Promise<void>): Promise<boolean> {
    const video = this.videoService.currentVideo();
    const isPostExtract = video && ['curating', 'curating_300', 'captioning', 'exported'].includes(video.status);

    let reason: string;
    if (isPostExtract) {
      reason = `Video status is "${video.status}". Modifying scene chunks will delete existing frames and curation picks.`;
    } else {
      try {
        await write(false);
        return true;
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        reason = err.message || 'Chunks hold active curation picks.';
      }
    }

    const ok = await this.confirm.ask({
      title: 'Confirm destructive re-chunking',
      description: `${reason} Candidate picks and curation history of the removed chunks are deleted. This cannot be undone.`,
      confirmLabel: 'Continue and delete',
    });
    if (!ok) return false;
    try {
      await write(true);
      return true;
    } catch (err) {
      this.jobEvents.showToast('Re-chunk failed', String(err), video?.id ?? null, true);
      return false;
    }
  }

  async onAutoSuggest(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      try {
        const done = await this.withRechunkConfirm((confirm) => this.chunkService.autoSuggest(video.id, confirm));
        if (!done) return;
        this.jobEvents.showToast(
          'Auto-Suggest Complete',
          `Generated ${this.chunkService.chunks().length} scene chunks using visual cuts!`,
          video.id
        );
      } catch (err) {
        this.jobEvents.showToast('Auto-Suggest Failed', `Error: ${err}`, video.id, true);
      }
    }
  }

  async onSyncCaptions(): Promise<void> {
    const video = this.videoService.currentVideo();
    await this.videoService.syncCaptions();
    if (video) {
      await this.chunkService.loadChunks(video.id);
    }
  }

  async onSaveChunks(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      try {
        const done = await this.withRechunkConfirm((confirm) => this.chunkService.saveChunks(video.id, confirm));
        if (!done) return;
        this.jobEvents.showToast(
          'Chunks Saved',
          `Saved ${this.chunkService.chunks().length} scene chunks!`,
          video.id
        );
      } catch (err) {
        this.jobEvents.showToast('Save Failed', `Error: ${err}`, video.id, true);
      }
    }
  }

  async onExtract750(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      try {
        await this.chunkService.extract750(video.id);
        this.jobEvents.showToast(
          'Extraction Dispatched',
          `Extracting & scoring candidates for ${video.name}...`,
          video.id
        );
      } catch (err) {
        this.jobEvents.showToast('Extraction Failed', `Error: ${err}`, video.id, true);
      }
    }
  }

  async onExtractSingleChunk(chunk: ChunkItem): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video || !chunk.id) {
      this.jobEvents.showToast(
        'Extraction Warning',
        'Please save chunks before extracting an individual scene.',
        video?.id ?? null,
        true
      );
      return;
    }

    try {
      if (this.chunkService.isDirty()) {
        const done = await this.withRechunkConfirm((confirm) => this.chunkService.saveChunks(video.id, confirm));
        if (!done) return;
      }
      await this.chunkService.extractChunk(chunk.id, {
        k: chunk.k,
        budget_pct: chunk.budget_pct,
      });
      this.jobEvents.showToast(
        'Scene Extraction Dispatched',
        `Extracting frames specifically for Scene #${chunk.position + 1} (${chunk.budget_pct}% budget)...`,
        video.id
      );
    } catch (err) {
      this.jobEvents.showToast('Scene Extraction Failed', `Error: ${err}`, video.id, true);
    }
  }
}
