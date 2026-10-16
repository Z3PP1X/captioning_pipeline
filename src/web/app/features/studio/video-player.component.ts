import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  effect,
  inject,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucidePlay,
  lucidePause,
  lucideChevronLeft,
  lucideChevronRight,
  lucideSparkles,
  lucideTag,
  lucideStepBack,
  lucideStepForward,
  lucidePlus,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { VideoService } from '../../core/services/video.service';
import { ChunkService, MAX_CHUNKS } from '../../core/services/chunk.service';
import { HotkeyService } from '../../core/services/hotkey.service';
import { JobEventService } from '../../core/services/job-event.service';
import { formatTime, formatShortTime, parseTimestamp } from '../../core/utils/time.utils';
import { WatermarkRect } from '../../core/models/video.model';

@Component({
  selector: 'app-video-player',
  imports: [NgIcon, HlmButtonImports, HlmNativeSelectImports],
  providers: [
    provideIcons({
      lucidePlay,
      lucidePause,
      lucideChevronLeft,
      lucideChevronRight,
      lucideSparkles,
      lucideTag,
      lucideStepBack,
      lucideStepForward,
      lucidePlus,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'block w-full max-w-full md:max-w-[80%] mx-auto',
  },
  template: `
    <div class="flex flex-col w-full bg-card rounded-xl border border-border overflow-hidden shadow-xs">
      <!-- Player Video & Watermark Canvas Stage -->
      <div class="relative w-full aspect-video max-h-[500px] bg-secondary flex items-center justify-center overflow-hidden">
        <video
          #videoEl
          class="max-w-full max-h-full object-contain cursor-pointer"
          (seeked)="onTimeUpdate()"
          (loadedmetadata)="onLoadedMetadata()"
          (play)="onPlay()"
          (pause)="onPause()"
          (ended)="onPause()"
          (click)="togglePlayPause()"
          preload="metadata"
          playsinline
        ></video>

        <!-- Watermark Exclusion Overlay Canvas -->
        <canvas
          #watermarkCanvas
          class="absolute inset-0 w-full h-full pointer-events-none"
          [class.pointer-events-auto]="isDrawingWatermark()"
          [class.cursor-crosshair]="isDrawingWatermark()"
          (mousedown)="onCanvasMouseDown($event)"
          (mousemove)="onCanvasMouseMove($event)"
          (mouseup)="onCanvasMouseUp()"
        ></canvas>

        @if (isDrawingWatermark()) {
          <div class="absolute top-3 left-3 right-3 z-20 flex items-center justify-between rounded-lg border border-info/40 bg-card/95 text-foreground px-3 py-1.5 text-xs shadow-lg animate-in fade-in">
            <div class="flex items-center gap-1.5 font-medium text-info">
              <ng-icon name="lucideSparkles" class="size-3.5" />
              <span>Draw Watermark Exclusion Box (Click & Drag on video)</span>
            </div>
            <div class="flex items-center gap-2">
              <button
                (click)="clearWatermark()"
                class="hover:underline text-xs text-muted-foreground hover:text-foreground cursor-pointer"
              >
                Clear
              </button>
              <button
                hlmBtn
                variant="outline"
                size="xs"
                (click)="toggleWatermarkMode()"
                class="h-6 cursor-pointer"
              >
                Done
              </button>
            </div>
          </div>
        }
      </div>

      <!-- Controls Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 p-3 border-t border-border bg-card text-xs">
        <!-- Left: Play/Pause, Timecode, Jumps, Frame Step -->
        <div class="flex flex-wrap items-center gap-2">
          <button
            hlmBtn
            variant="ghost"
            size="icon"
            (click)="togglePlayPause()"
            title="Play/Pause (Space)"
            class="size-8 cursor-pointer border border-border hover:bg-muted"
          >
            @if (isPlaying()) {
              <ng-icon name="lucidePause" class="size-4" />
            } @else {
              <ng-icon name="lucidePlay" class="size-4" />
            }
          </button>

          <!-- Timecode Display -->
          <div class="text-xs font-medium px-2 py-1 rounded-md bg-muted text-foreground tabular-nums">
            <span>{{ formatTime(currentTime()) }}</span> / <span>{{ formatTime(duration()) }}</span>
          </div>

          <!-- Jump Input -->
          <div class="flex items-center gap-1">
            <input
              #jumpInput
              type="text"
              [value]="jumpInputStr()"
              (input)="jumpInputStr.set($any($event.target).value)"
              (keydown.enter)="executeJump()"
              placeholder="Jump (e.g. 14:30)"
              title="Timestamp (MM:SS or seconds) Hotkey: G"
              class="h-7 w-24 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
            />
            <button
              hlmBtn
              variant="outline"
              size="xs"
              (click)="executeJump()"
              class="h-7 cursor-pointer"
            >
              Go
            </button>
          </div>

          <!-- Quick Jumps -->
          <div class="flex items-center rounded-md border border-border overflow-hidden">
            <button
              (click)="jumpDelta(-60)"
              class="px-1.5 py-1 hover:bg-muted text-xs text-muted-foreground border-r border-border tabular-nums"
              title="-60s (Ctrl+Left)"
            >
              -60s
            </button>
            <button
              (click)="jumpDelta(-10)"
              class="px-1.5 py-1 hover:bg-muted text-xs text-muted-foreground border-r border-border tabular-nums"
              title="-10s (Shift+Left)"
            >
              -10s
            </button>
            <button
              (click)="jumpDelta(10)"
              class="px-1.5 py-1 hover:bg-muted text-xs text-muted-foreground border-r border-border tabular-nums"
              title="+10s (Shift+Right)"
            >
              +10s
            </button>
            <button
              (click)="jumpDelta(60)"
              class="px-1.5 py-1 hover:bg-muted text-xs text-muted-foreground tabular-nums"
              title="+60s (Ctrl+Right)"
            >
              +60s
            </button>
          </div>

          <!-- Frame Stepping -->
          <div class="flex items-center gap-1">
            <button
              hlmBtn
              variant="outline"
              size="xs"
              (click)="stepFrame(-1)"
              title="Previous frame (,)"
              class="h-7 cursor-pointer gap-1"
            >
              <ng-icon name="lucideStepBack" class="size-3.5" aria-hidden="true" />
              <span>Frame</span>
            </button>
            <button
              hlmBtn
              variant="outline"
              size="xs"
              (click)="stepFrame(1)"
              title="Next frame (.)"
              class="h-7 cursor-pointer gap-1"
            >
              <span>Frame</span>
              <ng-icon name="lucideStepForward" class="size-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>

        <!-- Center: In/Out Markers, Add Chunk -->
        <div class="flex items-center gap-2">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="markIn()"
            title="Set In-Point ([ or I)"
            class="cursor-pointer"
          >
            [ In <span class="ml-1 font-semibold text-foreground tabular-nums">{{ formatShortTime(chunkService.inPoint()) }}</span>
          </button>
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="markOut()"
            title="Set Out-Point (] or O)"
            class="cursor-pointer"
          >
            ] Out <span class="ml-1 font-semibold text-foreground tabular-nums">{{ formatShortTime(chunkService.outPoint()) }}</span>
          </button>
          <button
            hlmBtn
            variant="secondary"
            size="sm"
            (click)="addChunk()"
            title="Add Scene Chunk (Enter)"
            class="cursor-pointer font-semibold gap-1"
          >
            <ng-icon name="lucidePlus" class="size-3.5" aria-hidden="true" />
            <span>Add Chunk</span>
            <kbd class="ml-1 rounded border border-border px-1 text-xs font-normal text-muted-foreground">Enter</kbd>
          </button>
        </div>

        <!-- Right: Snap Cuts, Speed, Watermark Toggle -->
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-1.5 cursor-pointer text-xs text-foreground">
            <input
              type="checkbox"
              [checked]="chunkService.snapCuts()"
              (change)="chunkService.snapCuts.set($any($event.target).checked)"
              class="rounded border-input text-primary focus:ring-ring"
            />
            <span>Snap Cuts <kbd class="text-xs text-muted-foreground">C</kbd></span>
          </label>

          <label for="player-speed" class="sr-only">Playback speed</label>
          <hlm-native-select
            size="sm"
            selectId="player-speed"
            selectClass="text-xs"
            [value]="'' + playbackSpeed()"
            (valueChange)="onSpeedChange(+($event || 1))"
          >
            @for (s of speeds; track s) {
              <option [value]="'' + s">{{ s }}×</option>
            }
          </hlm-native-select>

          <button
            hlmBtn
            [variant]="isDrawingWatermark() ? 'secondary' : 'outline'"
            size="sm"
            (click)="toggleWatermarkMode()"
            title="Draw exclusion rectangle for watermarks"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon name="lucideTag" class="size-3.5" />
            <span>Watermark Rect</span>
          </button>
        </div>
      </div>
    </div>
  `,
})
export class VideoPlayerComponent implements OnInit, AfterViewInit, OnDestroy {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);
  private readonly hotkeys = inject(HotkeyService);
  private readonly jobEvents = inject(JobEventService);

  // Signal queries: the source-loading effect re-runs once the <video> element exists (zoneless-safe)
  private readonly videoElRef = viewChild<ElementRef<HTMLVideoElement>>('videoEl');
  private readonly canvasRef = viewChild<ElementRef<HTMLCanvasElement>>('watermarkCanvas');
  private readonly jumpInputRef = viewChild<ElementRef<HTMLInputElement>>('jumpInput');

  readonly timeChange = output<number>();

  readonly isPlaying = signal<boolean>(false);
  readonly currentTime = signal<number>(0);
  readonly duration = signal<number>(0);
  readonly isDrawingWatermark = signal<boolean>(false);

  readonly jumpInputStr = signal<string>('');
  readonly playbackSpeed = signal<number>(1.0);
  readonly speeds = [0.25, 0.5, 1, 1.5, 2, 4, 8, 16] as const;

  private isDragging = false;
  private dragStartX = 0;
  private dragStartY = 0;
  private currentRect: WatermarkRect | null = null;
  private unregisterHotkeys: (() => void)[] = [];
  private rafId: number | null = null;

  readonly formatTime = formatTime;
  readonly formatShortTime = formatShortTime;

  constructor() {
    // When the current video changes (or the <video> element is first rendered), load the source
    effect(() => {
      const video = this.videoService.currentVideo();
      const ref = this.videoElRef();
      if (ref && video) {
        const el = ref.nativeElement;
        el.src = `/api/videos/${video.id}/stream`;
        el.load();
        this.currentRect = video.watermark_rect || null;
        untracked(() => this.drawWatermarkCanvas());
      }
    });
  }

  ngOnInit(): void {
    this.unregisterHotkeys.push(
      this.hotkeys.register('play_pause', () => this.togglePlayPause()),
      this.hotkeys.register('pause', () => this.videoElRef()?.nativeElement.pause()),
      this.hotkeys.register('seek_back_2', () => this.jumpDelta(-2)),
      this.hotkeys.register('seek_fwd_2', () => this.jumpDelta(2)),
      this.hotkeys.register('seek_back_300', () => this.jumpDelta(-300)),
      this.hotkeys.register('seek_fwd_300', () => this.jumpDelta(300)),
      this.hotkeys.register('frame_back', () => this.stepFrame(-1)),
      this.hotkeys.register('frame_fwd', () => this.stepFrame(1)),
      this.hotkeys.register('seek_back_1', () => this.jumpDelta(-1)),
      this.hotkeys.register('seek_fwd_1', () => this.jumpDelta(1)),
      this.hotkeys.register('seek_back_10', () => this.jumpDelta(-10)),
      this.hotkeys.register('seek_fwd_10', () => this.jumpDelta(10)),
      this.hotkeys.register('seek_back_60', () => this.jumpDelta(-60)),
      this.hotkeys.register('seek_fwd_60', () => this.jumpDelta(60)),
      this.hotkeys.register('mark_in', () => this.markIn()),
      this.hotkeys.register('mark_out', () => this.markOut()),
      this.hotkeys.register('add_chunk', () => this.addChunk()),
      this.hotkeys.register('toggle_snap_cuts', () =>
        this.chunkService.snapCuts.set(!this.chunkService.snapCuts())
      ),
      this.hotkeys.register('jump_focus', () => {
        this.jumpInputRef()?.nativeElement?.focus();
        this.jumpInputRef()?.nativeElement?.select();
      })
    );
  }

  ngOnDestroy(): void {
    for (const unreg of this.unregisterHotkeys) {
      unreg();
    }
    this.unregisterHotkeys = [];
    this.stopPlayheadLoop();
    this.resizeObserver?.disconnect();
  }

  ngAfterViewInit(): void {
    // The initial source is set by the effect above as soon as the view query resolves
    this.setupCanvasResize();
  }

  togglePlayPause(): void {
    const el = this.videoElRef()!.nativeElement;
    if (el.paused) {
      el.play();
    } else {
      el.pause();
    }
  }

  /** A seek requested before the metadata loaded (duration unknown); applied in onLoadedMetadata. */
  private pendingSeek: number | null = null;

  seek(time: number): void {
    const el = this.videoElRef()!.nativeElement;
    const dur = el.duration || this.duration();
    if (!dur || !Number.isFinite(dur)) {
      // Clamping to an unknown duration would jump to 0: remember the target until the metadata arrives
      this.pendingSeek = Math.max(0, time);
      this.currentTime.set(this.pendingSeek);
      return;
    }
    const clamped = Math.max(0, Math.min(dur, time));
    el.currentTime = clamped;
    this.currentTime.set(clamped);
    this.timeChange.emit(clamped);
  }

  jumpDelta(seconds: number): void {
    this.seek(this.currentTime() + seconds);
  }

  stepFrame(dir: number): void {
    const video = this.videoService.currentVideo();
    const fps = video?.fps || 25.0;
    const el = this.videoElRef()!.nativeElement;
    el.pause();
    this.seek(this.currentTime() + dir * (1.0 / fps));
  }

  executeJump(): void {
    const parsed = parseTimestamp(this.jumpInputStr());
    if (parsed !== null) {
      this.seek(parsed);
      this.jumpInputStr.set('');
    }
  }

  markIn(): void {
    let t = this.currentTime();
    t = this.chunkService.snapTimeToCut(t);
    this.chunkService.inPoint.set(parseFloat(t.toFixed(2)));
  }

  markOut(): void {
    let t = this.currentTime();
    t = this.chunkService.snapTimeToCut(t);
    this.chunkService.outPoint.set(parseFloat(t.toFixed(2)));
  }

  addChunk(): void {
    const inP = this.chunkService.inPoint();
    const outP = this.chunkService.outPoint();
    if (inP === null || outP === null) {
      this.jobEvents.showToast('Mark In and Out first', 'Set an In point ([ or I) and an Out point (] or O).', null, true);
      return;
    }
    // Out may be before In: the service uses min/max
    const result = this.chunkService.tryAddChunk(inP, outP);
    if (result === 'max_reached') {
      this.jobEvents.showToast(
        'Chunk limit reached',
        `A video can have at most ${MAX_CHUNKS} chunks. Delete or merge a chunk first.`,
        null,
        true
      );
    } else if (result === 'invalid_range') {
      this.jobEvents.showToast('Empty range', 'In and Out are at the same time. Move one of them first.', null, true);
    }
  }

  onTimeUpdate(): void {
    const el = this.videoElRef()!.nativeElement;
    if (this.currentTime() === el.currentTime) return;
    this.currentTime.set(el.currentTime);
    this.timeChange.emit(el.currentTime);
  }

  onPlay(): void {
    this.isPlaying.set(true);
    this.startPlayheadLoop();
  }

  onPause(): void {
    this.isPlaying.set(false);
    this.stopPlayheadLoop();
    this.onTimeUpdate();
  }

  /** F12: drive the playhead with requestAnimationFrame while playing (timeupdate fires only ~4 Hz). */
  private startPlayheadLoop(): void {
    if (this.rafId !== null || typeof requestAnimationFrame === 'undefined') return;
    const tick = () => {
      this.onTimeUpdate();
      const el = this.videoElRef()?.nativeElement;
      this.rafId = el && !el.paused && !el.ended ? requestAnimationFrame(tick) : null;
    };
    this.rafId = requestAnimationFrame(tick);
  }

  private stopPlayheadLoop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  onLoadedMetadata(): void {
    const el = this.videoElRef()!.nativeElement;
    this.duration.set(el.duration);
    if (this.pendingSeek !== null) {
      const target = this.pendingSeek;
      this.pendingSeek = null;
      this.seek(target);
    }
    this.onTimeUpdate();
    this.drawWatermarkCanvas();
  }

  onSpeedChange(speed: number): void {
    this.playbackSpeed.set(speed);
    this.videoElRef()!.nativeElement.playbackRate = speed;
  }

  toggleWatermarkMode(): void {
    this.isDrawingWatermark.update((v) => !v);
  }

  clearWatermark(): void {
    const video = this.videoService.currentVideo();
    if (video) {
      this.currentRect = null;
      this.videoService.saveWatermark(video.id, null);
      this.drawWatermarkCanvas();
    }
  }

  private resizeObserver: ResizeObserver | null = null;

  private setupCanvasResize(): void {
    const canvas = this.canvasRef()!.nativeElement;
    this.resizeObserver = new ResizeObserver(() => {
      canvas.width = canvas.clientWidth;
      canvas.height = canvas.clientHeight;
      this.drawWatermarkCanvas();
    });
    this.resizeObserver.observe(canvas);
  }

  onCanvasMouseDown(e: MouseEvent): void {
    if (!this.isDrawingWatermark()) return;
    const rect = this.canvasRef()!.nativeElement.getBoundingClientRect();
    this.isDragging = true;
    this.dragStartX = (e.clientX - rect.left) / rect.width;
    this.dragStartY = (e.clientY - rect.top) / rect.height;
  }

  onCanvasMouseMove(e: MouseEvent): void {
    if (!this.isDragging || !this.isDrawingWatermark()) return;
    const rect = this.canvasRef()!.nativeElement.getBoundingClientRect();
    const currentX = (e.clientX - rect.left) / rect.width;
    const currentY = (e.clientY - rect.top) / rect.height;

    const x = Math.min(this.dragStartX, currentX);
    const y = Math.min(this.dragStartY, currentY);
    const width = Math.abs(currentX - this.dragStartX);
    const height = Math.abs(currentY - this.dragStartY);

    this.currentRect = { x, y, width, height };
    this.drawWatermarkCanvas();
  }

  onCanvasMouseUp(): void {
    if (!this.isDragging || !this.isDrawingWatermark()) return;
    this.isDragging = false;
    const video = this.videoService.currentVideo();
    if (video && this.currentRect) {
      this.videoService.saveWatermark(video.id, this.currentRect);
    }
  }

  private drawWatermarkCanvas(): void {
    const canvas = this.canvasRef()?.nativeElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const r = this.currentRect || this.videoService.currentVideo()?.watermark_rect;
    if (!r) return;

    const px = r.x * canvas.width;
    const py = r.y * canvas.height;
    const pw = r.width * canvas.width;
    const ph = r.height * canvas.height;

    // Brand colour from the design token (no hex in code)
    const primary = getComputedStyle(canvas).getPropertyValue('--primary').trim() || 'currentColor';
    ctx.strokeStyle = primary;
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.strokeRect(px, py, pw, ph);

    ctx.globalAlpha = 0.15;
    ctx.fillStyle = primary;
    ctx.fillRect(px, py, pw, ph);
    ctx.globalAlpha = 1;
  }
}
