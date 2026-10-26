import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  ViewChild,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { formatHms, formatShortTime } from '../../core/utils/time.utils';
import { FilmstripThumbnail } from '../../core/models/video.model';
import { ChunkItem } from '../../core/models/chunk.model';

@Component({
  selector: 'app-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="relative w-full select-none pt-7 pb-1">
      <!-- Filmstrip Track Container -->
      <div
        #trackContainer
        class="group relative h-22 md:h-24 w-full rounded-lg border border-border bg-secondary overflow-hidden cursor-pointer"
        (mousedown)="onMouseDown($event)"
        (mousemove)="onMouseMove($event)"
        (mouseleave)="onMouseLeave()"
      >
        <!-- Filmstrip Layer -->
        <div class="absolute inset-0 flex h-full w-full pointer-events-none opacity-80">
          @for (thumb of filmstripThumbs(); track thumb.time_s) {
            <img
              [src]="thumb.url"
              alt="filmstrip frame"
              class="h-full flex-1 object-cover"
              loading="lazy"
            />
          }
        </div>

        <!-- Scene Cuts Layer -->
        <div class="absolute inset-0 pointer-events-none">
          @for (cut of chunkService.cuts(); track cut) {
            @let cutPct = getPercent(cut);
            <div
              class="absolute top-0 bottom-0 w-[2px] bg-background/80 z-10"
              [style.left.%]="cutPct"
              title="Scene cut at {{ cut.toFixed(2) }}s"
            ></div>
          }
        </div>

        <!-- Defined Chunks Regions -->
        <div class="absolute inset-0 pointer-events-none">
          @for (chunk of chunkService.chunks(); track chunk.position) {
            @let startPct = getPercent(chunk.start_s);
            @let widthPct = getPercent(chunk.end_s) - startPct;
            <div
              role="button"
              tabindex="0"
              class="absolute top-1.5 bottom-1.5 rounded-md border border-primary/70 bg-primary/25 z-10 flex items-center justify-center text-xs font-bold text-primary-foreground overflow-hidden cursor-pointer pointer-events-auto hover:bg-primary/40 transition-colors shadow-xs"
              [style.left.%]="startPct"
              [style.width.%]="widthPct"
              (click)="onChunkClick(chunk, $event)"
              (keydown.enter)="onChunkClick(chunk, $event)"
              (keydown.space)="onChunkClick(chunk, $event); $event.preventDefault()"
              title="Chunk #{{ chunk.position + 1 }} ({{ formatHms(chunk.start_s) }} - {{ formatHms(chunk.end_s) }}) Click to jump"
            >
              <span class="truncate px-1 drop-shadow">#{{ chunk.position + 1 }}</span>
            </div>
          }
        </div>

        <!-- In / Out Range Shading -->
        @if (chunkService.inPoint() !== null && chunkService.outPoint() !== null) {
          @let inPct = getPercent(chunkService.inPoint()!);
          @let outPct = getPercent(chunkService.outPoint()!);
          <div
            class="absolute top-0 bottom-0 bg-warning/30 border-x-2 border-warning z-20 pointer-events-none"
            [style.left.%]="inPct"
            [style.width.%]="outPct - inPct"
          ></div>
        }
      </div>

      <!-- Current State Marker (Playhead with Floating Timestamp Label) -->
      <div
        class="absolute top-0 bottom-1 w-[2px] z-30 pointer-events-none transition-none"
        [style.left.%]="playheadPercent()"
      >
        <!-- Floating Timestamp Badge -->
        <div
          class="absolute top-0 flex flex-col pointer-events-none z-40"
          [class]="playheadLabelClass()"
        >
          <span class="rounded-md bg-primary px-1.5 py-0.5 tabular-nums text-xs font-bold text-primary-foreground shadow-md whitespace-nowrap">
            {{ formatHms(currentTime()) }}
          </span>
          <div class="size-0 border-x-3 border-x-transparent border-t-3 border-t-primary self-center"></div>
        </div>

        <!-- Marker Circle Head -->
        <div class="absolute top-[22px] -left-1.5 size-3.5 rounded-full bg-primary ring-2 ring-background shadow-md"></div>

        <!-- Playhead Vertical Line Across Track -->
        <div class="absolute top-[28px] bottom-0 left-0 w-[2px] bg-primary shadow-md"></div>
      </div>

      <!-- Hover Preview Popup -->
      @if (hoverTime() !== null) {
        <div
          class="absolute bottom-full mb-1 -translate-x-1/2 rounded-md border border-border bg-card p-1.5 shadow-lg z-50 pointer-events-none flex flex-col items-center"
          [style.left.px]="hoverX()"
        >
          @if (hoverThumbUrl()) {
            <img [src]="hoverThumbUrl()!" alt="hover thumb" class="w-28 h-16 object-cover rounded mb-1" />
          }
          <span class="tabular-nums text-xs text-foreground">{{ formatHms(hoverTime()!) }}</span>
        </div>
      }
    </div>
  `,
})
export class TimelineComponent {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  @ViewChild('trackContainer') trackRef!: ElementRef<HTMLDivElement>;

  readonly currentTime = input<number>(0);
  readonly seek = output<number>();

  readonly hoverTime = signal<number | null>(null);
  readonly hoverX = signal<number>(0);
  readonly hoverThumbUrl = signal<string | null>(null);

  readonly formatHms = formatHms;
  readonly formatShortTime = formatShortTime;

  readonly duration = computed(() => {
    return this.videoService.currentVideo()?.duration || 1;
  });

  readonly filmstripThumbs = computed<FilmstripThumbnail[]>(() => {
    return this.chunkService.filmstrip()?.thumbnails || [];
  });

  readonly playheadPercent = computed(() => {
    const dur = this.duration();
    const cur = this.currentTime();
    return Math.max(0, Math.min(100, (cur / dur) * 100));
  });

  readonly playheadLabelClass = computed(() => {
    const pct = this.playheadPercent();
    if (pct < 4) return 'left-0 translate-x-0 items-start';
    if (pct > 96) return 'right-0 translate-x-0 items-end';
    return 'left-1/2 -translate-x-1/2 items-center';
  });

  getPercent(timeS: number): number {
    const dur = this.duration();
    return Math.max(0, Math.min(100, (timeS / dur) * 100));
  }

  onChunkClick(chunk: ChunkItem, e: Event): void {
    e.stopPropagation();
    this.seek.emit(chunk.start_s);
  }

  onMouseDown(e: MouseEvent): void {
    this.handleScrub(e);
  }

  onMouseMove(e: MouseEvent): void {
    const rect = this.trackRef.nativeElement.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const time = ratio * this.duration();

    this.hoverX.set(e.clientX - rect.left);
    this.hoverTime.set(time);

    // Find nearest filmstrip thumb
    const thumbs = this.filmstripThumbs();
    if (thumbs.length > 0) {
      let nearest = thumbs[0];
      let minDiff = Math.abs(nearest.time_s - time);
      for (const t of thumbs) {
        const diff = Math.abs(t.time_s - time);
        if (diff < minDiff) {
          minDiff = diff;
          nearest = t;
        }
      }
      this.hoverThumbUrl.set(nearest.url);
    }

    if (e.buttons === 1) {
      this.handleScrub(e);
    }
  }

  onMouseLeave(): void {
    this.hoverTime.set(null);
  }

  private handleScrub(e: MouseEvent): void {
    const rect = this.trackRef.nativeElement.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const time = ratio * this.duration();
    this.seek.emit(time);
  }
}
