import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  output,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideX,
  lucideStar,
  lucideArrowLeftRight,
  lucideArrowUp,
  lucidePlay,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { CurateService } from '../../core/services/curate.service';
import { ChunkService } from '../../core/services/chunk.service';
import { VideoService } from '../../core/services/video.service';
import { formatShortTime } from '../../core/utils/time.utils';
import { CandidateFrame, CandidateState } from '../../core/models/candidate.model';
import { ChunkItem } from '../../core/models/chunk.model';

export interface ChunkCandidateGroup {
  chunkId: number | string;
  chunkNumber: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  captionModel: string;
  tags: string;
  candidates: CandidateFrame[];
  picksCount: number;
  totalCount: number;
  captionedCount: number;
  isFullyCaptioned: boolean;
}

@Component({
  selector: 'app-candidate-grid',
  imports: [FormsModule, NgIcon, HlmButtonImports],
  providers: [
    provideIcons({
      lucideCheck,
      lucideX,
      lucideStar,
      lucideArrowLeftRight,
      lucideArrowUp,
      lucidePlay,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'flex-1 min-h-0 flex flex-col overflow-hidden w-full',
  },
  template: `
    <div class="flex-1 min-h-0 overflow-y-auto pr-1 pb-16 space-y-4">
      @for (group of chunkGroups(); track group.chunkId) {
        <!-- Chunk Container Section -->
        <section class="flex flex-col gap-3 rounded-xl border border-border bg-card p-3 md:p-4 shadow-xs">
          <!-- Chunk Section Header -->
          <div class="flex flex-wrap items-center justify-between gap-2.5 border-b border-border pb-2.5">
            <!-- Left: Chunk Badge, Time Range, Tags, Picks Count, Caption Status -->
            <div class="flex items-center gap-2 flex-wrap">
              <span class="rounded px-2 py-0.5 text-xs font-bold bg-primary/10 text-primary border border-primary/20 uppercase tracking-wider">
                Chunk #{{ group.chunkNumber }}
              </span>

              <span class="font-mono text-xs text-muted-foreground">
                {{ formatShortTime(group.start_s) }} → {{ formatShortTime(group.end_s) }} ({{ group.duration_s }}s)
              </span>

              @if (group.tags) {
                <span class="rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground font-medium">
                  🏷️ {{ group.tags }}
                </span>
              }

              <span class="rounded-full bg-muted/80 px-2 py-0.5 text-[11px] font-semibold text-foreground">
                {{ group.picksCount }} picks / {{ group.totalCount }} candidates
              </span>

              @if (group.isFullyCaptioned) {
                <span class="rounded px-1.5 py-0.5 text-[10px] font-bold bg-success/15 text-success border border-success/30">
                  ✓ Captioned ({{ group.captionedCount }}/{{ group.picksCount }})
                </span>
              } @else if (group.captionedCount > 0) {
                <span class="rounded px-1.5 py-0.5 text-[10px] font-medium bg-primary/10 text-primary border border-primary/20">
                  ⏳ {{ group.captionedCount }}/{{ group.picksCount }}
                </span>
              }
            </div>

            <!-- Right: Actions (Caption Model Selector, Keep All in Chunk, Deselect All in Chunk, Seek) -->
            <div class="flex items-center gap-2">
              <select
                [ngModel]="group.captionModel"
                (ngModelChange)="onModelChange(group.chunkId, $event)"
                class="h-7 rounded border border-input bg-card px-2 text-xs text-foreground focus:ring-1 focus:ring-ring cursor-pointer"
                title="Captioning Model for Chunk #{{ group.chunkNumber }}"
              >
                <option value="google/gemma-4-31b-it">Gemma 4 (Default)</option>
                <option value="deepseek/deepseek-v4.1-flash">DeepSeek V4.1 Flash</option>
                <option value="google/gemini-2.0-flash-exp:free">Gemini 2.0 Free</option>
                <option value="qwen/qwen-2.5-vl-72b-instruct">Qwen 2.5 VL 72B</option>
                <option value="x-ai/grok-4.7">Grok 4.7</option>
                <option value="mistralai/pixtral-12b">Pixtral 12B</option>
              </select>

              <button
                hlmBtn
                variant="outline"
                size="xs"
                (click)="onKeepChunk(group)"
                class="h-7 cursor-pointer gap-1"
                title="Keep all picks in Chunk #{{ group.chunkNumber }}"
              >
                <ng-icon name="lucideCheck" class="size-3 text-success" />
                <span>Keep All</span>
              </button>

              <button
                hlmBtn
                variant="outline"
                size="xs"
                (click)="onRejectChunk(group)"
                class="h-7 cursor-pointer gap-1"
                title="Deselect picks in Chunk #{{ group.chunkNumber }}"
              >
                <ng-icon name="lucideX" class="size-3 text-destructive" />
                <span>Deselect All</span>
              </button>

              <button
                hlmBtn
                variant="ghost"
                size="xs"
                (click)="onSeekChunk(group.start_s)"
                class="h-7 cursor-pointer gap-1 text-muted-foreground hover:text-foreground"
                title="Jump to Chunk #{{ group.chunkNumber }} start in Studio"
              >
                <ng-icon name="lucidePlay" class="size-3 text-primary" />
                <span>Seek</span>
              </button>
            </div>
          </div>

          <!-- Cards Grid for this Chunk -->
          <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-3">
            @for (c of group.candidates; track c.id) {
              @let isKept = c.state === 'keep' || (c.is_pick && c.state !== 'reject');
              @let isRejected = c.state === 'reject';
              @let isStarred = c.state === 'star';

              <div
                class="group relative flex flex-col rounded-lg border bg-card overflow-hidden transition-all text-xs"
                [class.border-success]="isStarred || (isKept && !isStarred)"
                [class.ring-1]="isStarred || (isKept && !isStarred)"
                [class.ring-success]="isKept && !isStarred"
                [class.ring-warning]="isStarred"
                [class.border-warning]="isStarred"
                [class.border-destructive]="isRejected"
                [class.opacity-40]="isRejected"
                [class.border-border]="!isKept && !isRejected && !isStarred"
              >
                <!-- Card Thumbnail (click to open Lightbox) -->
                <div
                  (click)="curateService.openLightbox(c.id)"
                  class="relative aspect-video w-full bg-black cursor-pointer overflow-hidden"
                >
                  <img
                    [src]="getThumbUrl(c)"
                    [alt]="'Frame at ' + (c.t !== undefined ? c.t : c.time_s)"
                    class="w-full h-full object-cover transition-transform group-hover:scale-105"
                    loading="lazy"
                  />

                  <!-- Top Badges Overlay -->
                  <div class="absolute top-1.5 left-1.5 right-1.5 flex items-center justify-between pointer-events-none text-[10px]">
                    <div class="flex items-center gap-1">
                      <span
                        class="rounded px-1.5 py-0.5 font-bold uppercase drop-shadow"
                        [class]="c.is_pick ? 'bg-primary text-primary-foreground' : 'bg-muted/80 text-foreground'"
                      >
                        {{ c.is_pick ? 'Pick' : 'Runner' }}
                      </span>

                      @if (c.is_captioned) {
                        <span class="rounded bg-success px-1.5 py-0.5 font-bold uppercase text-white drop-shadow">
                          ✓ Captioned
                        </span>
                      }
                    </div>

                    @if (c.score !== undefined) {
                      <span class="rounded bg-black/70 px-1.5 py-0.5 font-mono text-white">
                        {{ (c.score * 100).toFixed(0) }}
                      </span>
                    }
                  </div>

                  <!-- Bottom Timestamp Overlay -->
                  <div class="absolute bottom-1 left-1.5 pointer-events-none">
                    <span class="rounded bg-black/70 px-1 py-0.5 font-mono text-[10px] text-white">
                      {{ formatShortTime(c.t !== undefined ? c.t : c.time_s) }}
                    </span>
                  </div>
                </div>

                <!-- Card Actions Bar -->
                <div class="flex items-center justify-between p-1.5 bg-card border-t border-border">
                  <div class="flex items-center gap-1">
                    <button
                      (click)="onSetState(c, 'keep')"
                      class="p-1 rounded hover:bg-muted text-muted-foreground hover:text-success"
                      [class.text-success]="c.state === 'keep'"
                      title="Keep frame (K)"
                    >
                      <ng-icon name="lucideCheck" class="size-3.5" />
                    </button>

                    <button
                      (click)="onSetState(c, 'star')"
                      class="p-1 rounded hover:bg-muted text-muted-foreground hover:text-warning"
                      [class.text-warning]="c.state === 'star'"
                      title="Star frame (S)"
                    >
                      <ng-icon name="lucideStar" class="size-3.5" />
                    </button>

                    <button
                      (click)="onSetState(c, 'reject')"
                      class="p-1 rounded hover:bg-muted text-muted-foreground hover:text-destructive"
                      [class.text-destructive]="c.state === 'reject'"
                      title="Reject frame (X)"
                    >
                      <ng-icon name="lucideX" class="size-3.5" />
                    </button>
                  </div>

                  <div>
                    <button
                      (click)="onSwap(c)"
                      class="p-1 rounded hover:bg-muted text-muted-foreground hover:text-primary"
                      [title]="c.is_pick ? 'Swap with next best runner-up candidate' : 'Promote runner-up to pick'"
                    >
                      <ng-icon [name]="c.is_pick ? 'lucideArrowLeftRight' : 'lucideArrowUp'" class="size-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            }
          </div>
        </section>
      } @empty {
        <div class="py-16 text-center text-xs text-muted-foreground">
          No candidates match the current filter or none extracted yet.
        </div>
      }
    </div>
  `,
})
export class CandidateGridComponent {
  readonly curateService = inject(CurateService);
  readonly chunkService = inject(ChunkService);
  private readonly videoService = inject(VideoService);

  readonly seekChunk = output<number>();
  readonly formatShortTime = formatShortTime;

  readonly chunkGroups = computed<ChunkCandidateGroup[]>(() => {
    const filtered = this.curateService.filteredCandidates();
    const loadedChunks =
      this.curateService.curationChunks().length > 0
        ? this.curateService.curationChunks()
        : this.chunkService.chunks();

    // Map chunk_id or position to ChunkItem
    const chunkMap = new Map<string, ChunkItem>();
    loadedChunks.forEach((ch, idx) => {
      const keyId = ch.id !== null && ch.id !== undefined ? String(ch.id) : String(idx);
      chunkMap.set(keyId, ch);
      chunkMap.set(String(ch.position), ch);
    });

    // Group candidates by chunk_id
    const groupMap = new Map<string, CandidateFrame[]>();
    for (const c of filtered) {
      const key = String(c.chunk_id ?? 'unknown');
      if (!groupMap.has(key)) {
        groupMap.set(key, []);
      }
      groupMap.get(key)!.push(c);
    }

    const result: ChunkCandidateGroup[] = [];
    const seenGroupKeys = new Set<string>();

    // First process chunks known from chunk list (maintains timeline order)
    for (let idx = 0; idx < loadedChunks.length; idx++) {
      const ch = loadedChunks[idx];
      const key = ch.id !== null && ch.id !== undefined ? String(ch.id) : String(ch.position);
      seenGroupKeys.add(key);

      const cands = groupMap.get(key) || [];
      if (cands.length === 0) {
        continue;
      }

      const picksCount = cands.filter(
        (c) => c.state === 'keep' || (c.is_pick && c.state !== 'reject')
      ).length;
      const captionedCount = cands.filter((c) => c.is_captioned).length;

      const tagText =
        typeof ch.tags === 'string'
          ? ch.tags
          : Array.isArray(ch.tags)
            ? (ch.tags as string[]).join(', ')
            : '';

      result.push({
        chunkId: ch.id ?? ch.position,
        chunkNumber: ch.position !== undefined ? ch.position + 1 : idx + 1,
        start_s: ch.start_s || 0,
        end_s: ch.end_s || 0,
        duration_s: parseFloat(Math.max(0, (ch.end_s || 0) - (ch.start_s || 0)).toFixed(1)),
        captionModel: ch.caption_model || 'google/gemma-4-31b-it',
        tags: tagText,
        candidates: cands,
        picksCount,
        totalCount: cands.length,
        captionedCount,
        isFullyCaptioned: picksCount > 0 && captionedCount >= picksCount,
      });
    }

    // Next process any groups that didn't match loaded chunks by exact ID
    for (const [key, cands] of groupMap.entries()) {
      if (!seenGroupKeys.has(key) && cands.length > 0) {
        const first = cands[0];
        const chunkNum =
          first.chunk_pos !== undefined
            ? first.chunk_pos + 1
            : first.chunk_position !== undefined
              ? first.chunk_position + 1
              : 1;

        const start = first.chunk_start || 0;
        const end = first.chunk_end || 0;
        const picksCount = cands.filter(
          (c) => c.state === 'keep' || (c.is_pick && c.state !== 'reject')
        ).length;
        const captionedCount = cands.filter((c) => c.is_captioned).length;

        result.push({
          chunkId: key,
          chunkNumber: chunkNum,
          start_s: start,
          end_s: end,
          duration_s: parseFloat(Math.max(0, end - start).toFixed(1)),
          captionModel: first.caption_model || 'google/gemma-4-31b-it',
          tags: '',
          candidates: cands,
          picksCount,
          totalCount: cands.length,
          captionedCount,
          isFullyCaptioned: picksCount > 0 && captionedCount >= picksCount,
        });
      }
    }

    // Sort result by chunkNumber ascending
    return result.sort((a, b) => a.chunkNumber - b.chunkNumber);
  });

  getThumbUrl(c: CandidateFrame): string {
    if (c.preview_url) return c.preview_url;
    if (c.thumb_url) return c.thumb_url;
    return `/api/frames/${c.id}/preview`;
  }

  onSetState(c: CandidateFrame, state: CandidateState): void {
    const nextState = c.state === state ? null : state;
    this.curateService.setCandidateState(c.video_id, c.id, nextState);
  }

  onSwap(c: CandidateFrame): void {
    this.curateService.swapCandidate(c.video_id, c.id);
  }

  async onKeepChunk(group: ChunkCandidateGroup): Promise<void> {
    const unkept = group.candidates.filter((c) => c.state !== 'keep' && c.state !== 'star');
    for (const c of unkept) {
      await this.curateService.setCandidateState(c.video_id, c.id, 'keep');
    }
  }

  async onRejectChunk(group: ChunkCandidateGroup): Promise<void> {
    const activePicks = group.candidates.filter((c) => c.is_pick && c.state !== 'reject');
    for (const c of activePicks) {
      await this.curateService.setCandidateState(c.video_id, c.id, 'reject');
    }
  }

  async onModelChange(chunkId: number | string, model: string): Promise<void> {
    try {
      await this.chunkService.updateChunkModel(chunkId, model);
      // Also update curationChunks if present
      this.curateService.curationChunks.update((list) =>
        list.map((c) =>
          String(c.id) === String(chunkId) || String(c.position) === String(chunkId)
            ? { ...c, caption_model: model }
            : c
        )
      );
    } catch (err) {
      console.error(`Failed to update model for chunk ${chunkId}:`, err);
    }
  }

  onSeekChunk(start_s: number): void {
    this.seekChunk.emit(start_s);
  }
}
