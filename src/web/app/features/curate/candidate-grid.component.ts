import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  output,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideX,
  lucideStar,
  lucideArrowLeftRight,
  lucideArrowUp,
  lucidePlay,
  lucideClock,
  lucideFilm,
  lucideSearch,
  lucideTag,
  lucideTarget,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmSkeletonImports } from '@spartan-ng/helm/skeleton';
import { CurateService } from '../../core/services/curate.service';
import { ChunkService } from '../../core/services/chunk.service';
import { VideoService } from '../../core/services/video.service';
import { SettingsService } from '../../core/services/settings.service';
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
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmSkeletonImports],
  providers: [
    provideIcons({
      lucideCheck,
      lucideX,
      lucideStar,
      lucideArrowLeftRight,
      lucideArrowUp,
      lucidePlay,
      lucideClock,
      lucideFilm,
      lucideSearch,
      lucideTag,
      lucideTarget,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'flex-1 min-h-0 flex flex-col overflow-hidden w-full',
  },
  template: `
    <div class="flex-1 min-h-0 overflow-y-auto pr-1 pb-16 space-y-4">
      @if (curateService.isLoading()) {
        <!-- Skeleton loading state (B11) -->
        <div class="space-y-6 animate-in fade-in p-2">
          <div class="flex items-center gap-3">
            <span hlmSkeleton class="h-6 w-32 rounded-md"></span>
            <span hlmSkeleton class="h-6 w-48 rounded-md"></span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
            @for (i of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]; track i) {
              <div class="flex flex-col gap-2 rounded-xl border border-border p-2 bg-card">
                <span hlmSkeleton class="aspect-video w-full rounded-lg"></span>
                <div class="flex justify-between items-center px-1">
                  <span hlmSkeleton class="h-4 w-12 rounded"></span>
                  <span hlmSkeleton class="h-4 w-16 rounded"></span>
                </div>
              </div>
            }
          </div>
        </div>
      } @else {
        @for (group of chunkGroups(); track group.chunkId) {
        <!-- Chunk Container Section -->
        <section class="flex flex-col gap-3 rounded-xl border border-border bg-card p-3 md:p-4 shadow-xs">
          <!-- Chunk Section Header -->
          <div class="flex flex-wrap items-center justify-between gap-2.5 border-b border-border pb-2.5">
            <!-- Left: Chunk Badge, Time Range, Tags, Picks Count, Caption Status -->
            <div class="flex items-center gap-2 flex-wrap">
              <span class="rounded-md px-2 py-0.5 text-xs font-bold bg-primary/10 text-primary border border-primary/20 uppercase tracking-wider">
                Chunk #{{ group.chunkNumber }}
              </span>

              <span class="tabular-nums text-xs text-muted-foreground">
                {{ formatShortTime(group.start_s) }} → {{ formatShortTime(group.end_s) }} ({{ group.duration_s }}s)
              </span>

              @if (group.tags) {
                <span class="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground font-medium flex items-center gap-1">
                  <ng-icon name="lucideTag" class="size-3" />
                  <span>{{ group.tags }}</span>
                </span>
              }

              <span class="rounded-full bg-muted/80 px-2 py-0.5 text-xs font-semibold text-foreground tabular-nums">
                {{ group.picksCount }} picks / {{ group.totalCount }} candidates
              </span>

              @if (group.isFullyCaptioned) {
                <span
                  hlmBadge
                  variant="success"
                  class="gap-1 text-xs"
                >
                  <ng-icon name="lucideCheck" class="size-3" />
                  Captioned ({{ group.captionedCount }}/{{ group.picksCount }})
                </span>
              } @else if (group.captionedCount > 0) {
                <span
                  hlmBadge
                  variant="outline"
                  class="gap-1 text-xs"
                >
                  <ng-icon name="lucideClock" class="size-3" />
                  {{ group.captionedCount }}/{{ group.picksCount }}
                </span>
              }
            </div>

            <!-- Right: Actions (Caption Model Selector, Keep All in Chunk, Deselect All in Chunk, Seek) -->
            <div class="flex items-center gap-2">
              <select
                [value]="group.captionModel"
                (change)="onModelChange(group.chunkId, $any($event.target).value)"
                class="h-7 rounded border border-input bg-card px-2 text-xs text-foreground focus:ring-1 focus:ring-ring cursor-pointer"
                title="Captioning Model for Chunk #{{ group.chunkNumber }}"
              >
                @for (m of settingsService.openRouterModels(); track m.id) {
                  <option [value]="m.id">{{ m.name }}</option>
                }
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
                  (keydown.enter)="curateService.openLightbox(c.id)"
                  (keydown.space)="curateService.openLightbox(c.id); $event.preventDefault()"
                  role="button"
                  tabindex="0"
                  [attr.aria-label]="'Frame at ' + formatShortTime(c.t !== undefined ? c.t : c.time_s) + '. Open in lightbox'"
                  class="relative aspect-video w-full bg-secondary cursor-pointer overflow-hidden focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <img
                    [src]="getThumbUrl(c)"
                    [alt]="'Frame at ' + (c.t !== undefined ? c.t : c.time_s)"
                    class="w-full h-full object-cover transition-transform motion-safe:group-hover:scale-105"
                    loading="lazy"
                  />

                  <!-- Top Badges Overlay -->
                  <div class="absolute top-1.5 left-1.5 right-1.5 flex items-center justify-between pointer-events-none text-xs">
                    <div class="flex items-center gap-1">
                      <span
                        class="rounded-md px-1.5 py-0.5 font-bold uppercase drop-shadow text-xs"
                        [class]="c.is_pick ? 'bg-primary text-primary-foreground' : 'bg-muted/80 text-foreground'"
                      >
                        {{ c.is_pick ? 'Pick' : 'Runner' }}
                      </span>

                      @if (isStarred) {
                        <span hlmBadge variant="warning" class="shrink-0 text-[10px] px-1 py-0 h-4 font-bold drop-shadow">
                          Star
                        </span>
                      } @else if (isKept) {
                        <span hlmBadge variant="success" class="shrink-0 text-[10px] px-1 py-0 h-4 font-bold drop-shadow">
                          Kept
                        </span>
                      } @else if (isRejected) {
                        <span hlmBadge variant="destructive" class="shrink-0 text-[10px] px-1 py-0 h-4 font-bold drop-shadow">
                          Reject
                        </span>
                      }

                      @if (c.is_captioned) {
                        <span
                          hlmBadge
                          variant="outline"
                          class="shrink-0 text-xs px-1.5 py-0 bg-background/90 font-medium gap-1"
                          title="Captioned"
                        >
                          <ng-icon name="lucideCheck" class="size-2.5 text-success" />
                          Captioned
                        </span>
                      }

                      @if (c.target_person_matched || (c.target_person_sim && c.target_person_sim >= 0.363)) {
                        <span
                          hlmBadge
                          variant="secondary"
                          class="shrink-0 text-[10px] px-1 py-0 h-4 font-bold bg-primary/20 text-primary border border-primary/30 drop-shadow"
                          [title]="'Target Person Match: ' + Math.round((c.target_person_sim || 0) * 100) + '%'"
                        >
                          🎯 {{ Math.round((c.target_person_sim || 0) * 100) }}%
                        </span>
                      }
                    </div>

                    @if (c.score !== undefined) {
                      <span class="rounded-md bg-secondary/80 px-1.5 py-0.5 text-xs tabular-nums text-white">
                        {{ (c.score * 100).toFixed(0) }}
                      </span>
                    }
                  </div>

                  <!-- Bottom Timestamp Overlay -->
                  <div class="absolute bottom-1 left-1.5 pointer-events-none">
                    <span class="rounded-md bg-secondary/80 px-1 py-0.5 tabular-nums text-xs text-white">
                      {{ formatShortTime(c.t !== undefined ? c.t : c.time_s) }}
                    </span>
                  </div>
                </div>

                <!-- Card Actions Bar -->
                <div class="flex items-center justify-between p-1.5 bg-card border-t border-border">
                  <div class="flex items-center gap-1">
                    <button
                      (click)="onSetState(c, 'keep')"
                      class="size-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-success cursor-pointer"
                      [class.text-success]="c.state === 'keep'"
                      title="Keep frame (K)"
                      aria-label="Keep frame (K)"
                    >
                      <ng-icon name="lucideCheck" class="size-3.5" />
                    </button>

                    <button
                      (click)="onSetState(c, 'star')"
                      class="size-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-warning cursor-pointer"
                      [class.text-warning]="c.state === 'star'"
                      title="Star frame (S)"
                      aria-label="Star frame (S)"
                    >
                      <ng-icon name="lucideStar" class="size-3.5" />
                    </button>

                    <button
                      (click)="onSetState(c, 'reject')"
                      class="size-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-destructive cursor-pointer"
                      [class.text-destructive]="c.state === 'reject'"
                      title="Reject frame (X)"
                      aria-label="Reject frame (X)"
                    >
                      <ng-icon name="lucideX" class="size-3.5" />
                    </button>
                  </div>

                  <div class="flex items-center gap-1">
                    <button
                      (click)="tagPerson.emit(c.id)"
                      class="size-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-primary cursor-pointer"
                      title="Tag person from this frame"
                      aria-label="Tag person from this frame"
                    >
                      <ng-icon name="lucideTarget" class="size-3.5" />
                    </button>

                    <button
                      (click)="onSwap(c)"
                      class="size-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-primary cursor-pointer"
                      [title]="c.is_pick ? 'Swap with next best runner-up candidate' : 'Promote runner-up to pick'"
                      [attr.aria-label]="c.is_pick ? 'Swap with runner-up' : 'Promote runner-up to pick'"
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
        @if (curateService.candidates().length === 0) {
          <div class="flex flex-col items-center justify-center py-20 text-center gap-2">
            <ng-icon name="lucideFilm" class="size-8 text-muted-foreground/60" />
            <p class="font-bold text-sm text-foreground">No Candidates Extracted</p>
            <p class="text-xs text-muted-foreground max-w-sm">
              No frames have been extracted for this video yet. Go to the Studio tab and click "Extract & Score 750" to generate candidate frames.
            </p>
          </div>
        } @else {
          <div class="flex flex-col items-center justify-center py-20 text-center gap-2">
            <ng-icon name="lucideSearch" class="size-8 text-muted-foreground/60" />
            <p class="font-bold text-sm text-foreground">No Matching Candidates</p>
            <p class="text-xs text-muted-foreground max-w-sm">
              No candidates match the active role or category filters. Try clearing or expanding your filters in the toolbar above.
            </p>
          </div>
        }
      }
    }
    </div>
  `,
})
export class CandidateGridComponent {
  readonly curateService = inject(CurateService);
  readonly chunkService = inject(ChunkService);
  private readonly videoService = inject(VideoService);
  readonly settingsService = inject(SettingsService);

  readonly seekChunk = output<number>();
  readonly tagPerson = output<number>();
  readonly formatShortTime = formatShortTime;
  readonly Math = Math;

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
