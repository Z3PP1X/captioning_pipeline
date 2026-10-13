import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  ViewChild,
  computed,
  effect,
  inject,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucidePlay,
  lucidePause,
  lucideChevronLeft,
  lucideChevronRight,
  lucideSparkles,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { formatTime, formatShortTime, parseTimestamp } from '../../core/utils/time.utils';
import { WatermarkRect } from '../../core/models/video.model';

@Component({
  selector: 'app-video-player',
  imports: [FormsModule, NgIcon, HlmButtonImports],
  providers: [
    provideIcons({
      lucidePlay,
      lucidePause,
      lucideChevronLeft,
      lucideChevronRight,
      lucideSparkles,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'block w-full max-w-full md:max-w-[80%] mx-auto',
  },
  template: `
    <div class="flex flex-col w-full bg-card rounded-xl border border-border overflow-hidden shadow-xs">
      <!-- Player Video & Watermark Canvas Stage -->
      <div class="relative w-full aspect-video max-h-[500px] bg-black flex items-center justify-center overflow-hidden">
        <video
          #videoEl
          class="max-w-full max-h-full object-contain cursor-pointer"
          (timeupdate)="onTimeUpdate()"
          (loadedmetadata)="onLoadedMetadata()"
          (play)="isPlaying.set(true)"
          (pause)="isPlaying.set(false)"
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
          <div class="absolute top-3 left-3 bg-destructive/90 text-destructive-foreground px-3 py-1.5 rounded-md text-xs font-semibold shadow-md flex items-center gap-2">
            <span>Draw exclusion rectangle on video</span>
            <button
              (click)="clearWatermark()"
              class="underline hover:text-white ml-2 text-[11px]"
            >
              Clear
            </button>
            <button
              (click)="toggleWatermarkMode()"
              class="underline hover:text-white text-[11px]"
            >
              Done
            </button>
          </div>
        }
      </div>

      <!-- Controls Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 p-3 border-t border-border bg-card text-xs">
        <!-- Left: Play/Pause, Timecode, Jumps, Frame Step -->
        <div class="flex flex-wrap items-center gap-2">
          <button
            hlmBtn
            variant="default"
            size="icon"
            (click)="togglePlayPause()"
            title="Play/Pause (Space)"
            class="size-8 cursor-pointer"
          >
            @if (isPlaying()) {
              <ng-icon name="lucidePause" class="size-4" />
            } @else {
              <ng-icon name="lucidePlay" class="size-4" />
            }
          </button>

          <!-- Timecode Display -->
          <div class="font-mono text-xs font-medium px-2 py-1 rounded bg-muted text-foreground tabular-nums">
            <span>{{ formatTime(currentTime()) }}</span> / <span>{{ formatTime(duration()) }}</span>
          </div>

          <!-- Jump Input -->
          <div class="flex items-center gap-1">
            <input
              type="text"
              [(ngModel)]="jumpInputStr"
              (keydown.enter)="executeJump()"
              placeholder="Jump (e.g. 14:30)"
              title="Timestamp (MM:SS or seconds) Hotkey: G"
              class="h-7 w-24 rounded border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
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
              class="px-1.5 py-1 hover:bg-muted text-[11px] font-mono text-muted-foreground border-r border-border"
              title="-60s (Ctrl+Left)"
            >
              -60s
            </button>
            <button
              (click)="jumpDelta(-10)"
              class="px-1.5 py-1 hover:bg-muted text-[11px] font-mono text-muted-foreground border-r border-border"
              title="-10s (Shift+Left)"
            >
              -10s
            </button>
            <button
              (click)="jumpDelta(10)"
              class="px-1.5 py-1 hover:bg-muted text-[11px] font-mono text-muted-foreground border-r border-border"
              title="+10s (Shift+Right)"
            >
              +10s
            </button>
            <button
              (click)="jumpDelta(60)"
              class="px-1.5 py-1 hover:bg-muted text-[11px] font-mono text-muted-foreground"
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
              class="h-7 cursor-pointer"
            >
              ◀ Frame
            </button>
            <button
              hlmBtn
              variant="outline"
              size="xs"
              (click)="stepFrame(1)"
              title="Next frame (.)"
              class="h-7 cursor-pointer"
            >
              Frame ▶
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
            [ In <span class="font-mono ml-1 text-primary">{{ formatShortTime(chunkService.inPoint()) }}</span>
          </button>
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="markOut()"
            title="Set Out-Point (] or O)"
            class="cursor-pointer"
          >
            ] Out <span class="font-mono ml-1 text-primary">{{ formatShortTime(chunkService.outPoint()) }}</span>
          </button>
          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="addChunk()"
            title="Add Scene Chunk (Enter)"
            class="cursor-pointer font-semibold"
          >
            + Add Chunk <kbd class="ml-1 text-[10px] opacity-75">↵</kbd>
          </button>
        </div>

        <!-- Right: Snap Cuts, Speed, Watermark Toggle -->
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-1.5 cursor-pointer text-xs text-foreground">
            <input
              type="checkbox"
              [ngModel]="chunkService.snapCuts()"
              (ngModelChange)="chunkService.snapCuts.set($event)"
              class="rounded border-input text-primary focus:ring-ring"
            />
            <span>Snap Cuts <kbd class="font-mono text-[10px] text-muted-foreground">C</kbd></span>
          </label>

          <select
            [(ngModel)]="playbackSpeed"
            (ngModelChange)="onSpeedChange($event)"
            class="h-7 rounded border border-input bg-background px-1.5 text-xs text-foreground focus:ring-1 focus:ring-ring"
          >
            <option [value]="0.25">0.25×</option>
            <option [value]="0.5">0.5×</option>
            <option [value]="1.0">1.0×</option>
            <option [value]="1.5">1.5×</option>
            <option [value]="2.0">2.0×</option>
            <option [value]="4.0">4.0×</option>
            <option [value]="8.0">8.0×</option>
            <option [value]="16.0">16.0×</option>
          </select>

          <button
            hlmBtn
            [variant]="isDrawingWatermark() ? 'destructive' : 'outline'"
            size="sm"
            (click)="toggleWatermarkMode()"
            title="Draw exclusion rectangle for watermarks"
            class="cursor-pointer"
          >
            🏷️ Watermark Rect
          </button>
        </div>
      </div>
    </div>
  `,
})
export class VideoPlayerComponent implements AfterViewInit {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  @ViewChild('videoEl') videoElRef!: ElementRef<HTMLVideoElement>;
  @ViewChild('watermarkCanvas') canvasRef!: ElementRef<HTMLCanvasElement>;

  readonly timeChange = output<number>();

  readonly isPlaying = signal<boolean>(false);
  readonly currentTime = signal<number>(0);
  readonly duration = signal<number>(0);
  readonly isDrawingWatermark = signal<boolean>(false);

  jumpInputStr = '';
  playbackSpeed = 1.0;

  private isDragging = false;
  private dragStartX = 0;
  private dragStartY = 0;
  private currentRect: WatermarkRect | null = null;

  readonly formatTime = formatTime;
  readonly formatShortTime = formatShortTime;

  constructor() {
    // When current video changes, reload source
    effect(() => {
      const video = this.videoService.currentVideo();
      if (this.videoElRef && video) {
        const el = this.videoElRef.nativeElement;
        el.src = `/api/videos/${video.id}/stream`;
        el.load();
        this.currentRect = video.watermark_rect || null;
        this.drawWatermarkCanvas();
      }
    });
  }

  ngAfterViewInit(): void {
    const video = this.videoService.currentVideo();
    if (video && this.videoElRef) {
      const el = this.videoElRef.nativeElement;
      el.src = `/api/videos/${video.id}/stream`;
      el.load();
    }
    this.setupCanvasResize();
  }

  togglePlayPause(): void {
    const el = this.videoElRef.nativeElement;
    if (el.paused) {
      el.play();
    } else {
      el.pause();
    }
  }

  seek(time: number): void {
    const el = this.videoElRef.nativeElement;
    const dur = el.duration || this.duration();
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
    const el = this.videoElRef.nativeElement;
    el.pause();
    this.seek(this.currentTime() + dir * (1.0 / fps));
  }

  executeJump(): void {
    const parsed = parseTimestamp(this.jumpInputStr);
    if (parsed !== null) {
      this.seek(parsed);
      this.jumpInputStr = '';
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
    if (inP !== null && outP !== null && outP > inP) {
      this.chunkService.addChunk(inP, outP);
    } else {
      alert('Please set both valid In and Out points first (Out > In).');
    }
  }

  onTimeUpdate(): void {
    const el = this.videoElRef.nativeElement;
    this.currentTime.set(el.currentTime);
    this.timeChange.emit(el.currentTime);
  }

  onLoadedMetadata(): void {
    const el = this.videoElRef.nativeElement;
    this.duration.set(el.duration);
    this.drawWatermarkCanvas();
  }

  onSpeedChange(speed: number): void {
    this.playbackSpeed = speed;
    this.videoElRef.nativeElement.playbackRate = speed;
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

  private setupCanvasResize(): void {
    const canvas = this.canvasRef.nativeElement;
    const resizeObserver = new ResizeObserver(() => {
      canvas.width = canvas.clientWidth;
      canvas.height = canvas.clientHeight;
      this.drawWatermarkCanvas();
    });
    resizeObserver.observe(canvas);
  }

  onCanvasMouseDown(e: MouseEvent): void {
    if (!this.isDrawingWatermark()) return;
    const rect = this.canvasRef.nativeElement.getBoundingClientRect();
    this.isDragging = true;
    this.dragStartX = (e.clientX - rect.left) / rect.width;
    this.dragStartY = (e.clientY - rect.top) / rect.height;
  }

  onCanvasMouseMove(e: MouseEvent): void {
    if (!this.isDragging || !this.isDrawingWatermark()) return;
    const rect = this.canvasRef.nativeElement.getBoundingClientRect();
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
    const canvas = this.canvasRef?.nativeElement;
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

    ctx.strokeStyle = '#D73816'; // primary brand accent
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.strokeRect(px, py, pw, ph);

    ctx.fillStyle = 'rgba(215, 56, 22, 0.15)';
    ctx.fillRect(px, py, pw, ph);
  }
}
