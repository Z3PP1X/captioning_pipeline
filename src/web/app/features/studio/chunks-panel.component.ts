import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideTrash2,
  lucideSparkles,
  lucideSave,
  lucideZap,
  lucidePlay,
  lucideRefreshCw,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { ChunkService } from '../../core/services/chunk.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { formatHms, parseTimestamp } from '../../core/utils/time.utils';
import { ChunkItem } from '../../core/models/chunk.model';

@Component({
  selector: 'app-chunks-panel',
  imports: [FormsModule, NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideTrash2,
      lucideSparkles,
      lucideSave,
      lucideZap,
      lucidePlay,
      lucideRefreshCw,
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
            {{ chunkService.chunks().length }} / 15 max
          </span>
          <span
            class="rounded-full px-2.5 py-0.5 text-xs font-semibold border"
            [class]="budgetBadgeClass()"
          >
            Budget: {{ chunkService.budgetSum() }}%
          </span>
        </div>

        <div class="flex items-center gap-2">
          <select
            (change)="onBulkModelChange($any($event.target).value); $any($event.target).value = ''"
            class="h-7 rounded border border-input bg-card px-2 text-xs text-foreground focus:ring-1 focus:ring-ring cursor-pointer"
            title="Set captioning model for all chunks"
          >
            <option value="" disabled selected>Set All Models...</option>
            <option value="google/gemma-4-31b-it">Gemma 4 31B (Default)</option>
            <option value="deepseek/deepseek-v4.1-flash">DeepSeek V4.1 Flash</option>
            <option value="google/gemini-2.0-flash-exp:free">Gemini 2.0 Free</option>
            <option value="qwen/qwen-2.5-vl-72b-instruct">Qwen 2.5 VL 72B</option>
            <option value="x-ai/grok-4.7">Grok 4.7</option>
            <option value="mistralai/pixtral-12b">Pixtral 12B</option>
          </select>

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

          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="onExtract750()"
            [disabled]="!videoService.currentVideo() || chunkService.chunks().length === 0 || chunkService.isExtracting() || !!activeExtractionJob()"
            [title]="chunkService.chunks().length === 0 ? 'Click Auto-Suggest first to define scene chunks' : (activeExtractionJob() ? 'Extraction actively running' : 'Extract 750 candidates and run 7-dimension scoring')"
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
            <span class="font-mono font-bold">{{ (activeExtractionJob()!.progress * 100).toFixed(0) }}%</span>
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
          <div class="grid grid-cols-[135px_95px_95px_85px_1fr_110px_165px_36px] items-center gap-2.5 px-3 py-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/50 min-w-[840px]">
            <div>Chunk</div>
            <div>Start</div>
            <div>End</div>
            <div>Duration</div>
            <div>Frame Budget</div>
            <div>Rule</div>
            <div>Model</div>
            <div class="text-right">Action</div>
          </div>
        }

        @for (chunk of chunkService.chunks(); track chunk.position; let idx = $index) {
          <div
            (click)="seek.emit(chunk.start_s)"
            class="grid grid-cols-[135px_95px_95px_85px_1fr_110px_165px_36px] items-center gap-2.5 px-3 py-2 rounded-lg border transition-all text-xs cursor-pointer min-w-[840px]"
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
                  <span class="shrink-0 rounded px-1.5 py-0.2 text-[10px] font-bold bg-success/15 text-success border border-success/30" title="Captioned">
                    ✓
                  </span>
                } @else {
                  <span class="shrink-0 rounded px-1 py-0.2 text-[10px] font-medium bg-primary/10 text-primary border border-primary/20" title="Progress">
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
                class="h-7 w-21 rounded border border-input bg-card px-2 text-center font-mono text-xs text-foreground focus:ring-1 focus:ring-ring"
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
                class="h-7 w-21 rounded border border-input bg-card px-2 text-center font-mono text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <!-- Column 4: Duration (hh:mm:ss format) -->
            <div class="flex items-center font-mono text-xs text-muted-foreground">
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
                [ngModel]="chunk.budget_pct"
                (ngModelChange)="onBudgetChange(idx, $event)"
                class="flex-1 accent-primary h-1.5 bg-muted rounded-lg cursor-pointer min-w-16"
              />
              <span class="font-bold text-primary text-xs w-8 text-right tabular-nums">
                {{ chunk.budget_pct }}%
              </span>
            </div>

            <!-- Column 6: Rule Selector -->
            <div (click)="$event.stopPropagation()">
              <select
                [ngModel]="chunk.rule"
                (ngModelChange)="onRuleChange(idx, $event)"
                class="h-7 w-full rounded border border-input bg-card px-1.5 text-xs text-foreground focus:ring-1 focus:ring-ring cursor-pointer"
              >
                <option value="person">Person</option>
                <option value="equipment">Equipment</option>
                <option value="both">Both</option>
                <option value="either">Either</option>
                <option value="none">None (Quality)</option>
              </select>
            </div>

            <!-- Column 7: Model Selector -->
            <div (click)="$event.stopPropagation()">
              <select
                [ngModel]="chunk.caption_model || 'google/gemma-4-31b-it'"
                (ngModelChange)="onModelChange(idx, $event)"
                class="h-7 w-full rounded border border-input bg-card px-1.5 text-xs text-foreground focus:ring-1 focus:ring-ring cursor-pointer truncate"
              >
                <option value="google/gemma-4-31b-it">Gemma 4 31B (Default)</option>
                <option value="deepseek/deepseek-v4.1-flash">DeepSeek V4.1 Flash</option>
                <option value="google/gemini-2.0-flash-exp:free">Gemini 2.0 Free</option>
                <option value="qwen/qwen-2.5-vl-72b-instruct">Qwen 2.5 VL 72B</option>
                <option value="x-ai/grok-4.7">Grok 4.7</option>
                <option value="mistralai/pixtral-12b">Pixtral 12B</option>
              </select>
            </div>

            <!-- Column 8: Delete Action -->
            <div class="flex items-center justify-end" (click)="$event.stopPropagation()">
              <button
                type="button"
                (click)="chunkService.deleteChunk(idx)"
                class="text-muted-foreground hover:text-destructive p-1 rounded cursor-pointer transition-colors"
                title="Remove this chunk"
              >
                <ng-icon name="lucideTrash2" class="size-4" />
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

  readonly budgetBadgeClass = computed(() => {
    const sum = this.chunkService.budgetSum();
    if (sum === 100) return 'bg-success/15 text-success border-success/30';
    if (sum > 100) return 'bg-primary/20 text-primary border-primary/40';
    return 'bg-warning/15 text-warning border-warning/30';
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

  async onAutoSuggest(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      try {
        await this.chunkService.autoSuggest(video.id);
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
        await this.chunkService.saveChunks(video.id);
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
}
