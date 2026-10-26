import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  computed,
  input,
  output,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideAlertTriangle,
  lucideCheck,
  lucideCrop,
  lucideLayers,
  lucideMaximize2,
  lucideSparkles,
} from '@ng-icons/lucide';
import { BorderDirection, SmartCropBox, WatermarkBox } from '../../core/models/moss.model';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';

export interface SvgRect {
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string;
  targetBucket?: string;
}

export function normalizeToSvg(
  box: { x: number; y: number; width: number; height: number },
  vWidth: number,
  vHeight: number,
): { x: number; y: number; width: number; height: number } {
  // If coordinates are normalized in [0, 1] range (where width <= 1.01 and height <= 1.01)
  const isNormalized = box.width <= 1.05 && box.height <= 1.05 && box.x <= 1.05 && box.y <= 1.05;
  if (isNormalized) {
    return {
      x: Math.round(box.x * vWidth),
      y: Math.round(box.y * vHeight),
      width: Math.round(box.width * vWidth),
      height: Math.round(box.height * vHeight),
    };
  }
  return {
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
  };
}

@Component({
  selector: 'app-watermark-crop-overlay',
  imports: [NgIcon, HlmBadgeImports, HlmButtonImports],
  providers: [
    provideIcons({
      lucideCrop,
      lucideLayers,
      lucideCheck,
      lucideSparkles,
      lucideAlertTriangle,
      lucideMaximize2,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="relative flex flex-col gap-3 w-full rounded-xl border border-border bg-card p-4 text-card-foreground shadow-sm">
      <!-- Top Action Bar -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div class="flex items-center gap-2">
          <h3 class="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
            <ng-icon name="lucideCrop" class="size-4 text-primary" />
            <span>Visual Watermark & Smart-Crop Overlay</span>
          </h3>

          @if (smartCrop()) {
            <span
              hlmBadge
              variant="outline"
              class="border-emerald-500/40 text-emerald-500 bg-emerald-500/10 text-[11px] font-mono"
            >
              Bucket: {{ smartCrop()?.targetBucket || 'Auto' }}
            </span>
          }

          @if (watermarks().length > 0) {
            <span
              hlmBadge
              variant="destructive"
              class="text-[11px] font-mono"
            >
              {{ watermarks().length }} Watermark{{ watermarks().length > 1 ? 's' : '' }}
            </span>
          }
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center gap-2">
          <button
            type="button"
            hlmButton
            size="sm"
            variant="default"
            class="h-8 gap-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white"
            [disabled]="!smartCrop()"
            (click)="onApplyCrop()"
            title="Smart-Crop anwenden [Taste: C]"
          >
            <ng-icon name="lucideCheck" class="size-3.5" />
            <span>[C] Smart-Crop anwenden</span>
          </button>

          <button
            type="button"
            hlmButton
            size="sm"
            variant="outline"
            class="h-8 gap-1.5 text-xs font-semibold border-emerald-500/40 text-emerald-500 hover:bg-emerald-500/10"
            [disabled]="!smartCrop()"
            (click)="onApplyCropToAll()"
            title="Auf alle Frames mit identischem Wasserzeichen anwenden [Taste: Shift+C]"
          >
            <ng-icon name="lucideLayers" class="size-3.5" />
            <span>[Shift+C] Auf alle Frames anwenden</span>
          </button>
        </div>
      </div>

      <!-- Preview Stage with SVG Overlay -->
      <div
        class="relative w-full overflow-hidden rounded-lg border border-border/80 bg-neutral-950 flex items-center justify-center select-none"
        [style.aspectRatio]="aspectRatio()"
      >
        <!-- Background Image Preview -->
        @if (imageUrl()) {
          <img
            [src]="imageUrl()"
            alt="Vorschau"
            class="absolute inset-0 h-full w-full object-contain pointer-events-none"
          />
        } @else {
          <div class="flex flex-col items-center justify-center gap-2 text-muted-foreground p-8">
            <ng-icon name="lucideMaximize2" class="size-8 stroke-[1.5] text-muted-foreground/60" />
            <span class="text-xs font-mono">Kein Bild geladen ({{ viewWidth() }}&times;{{ viewHeight() }})</span>
          </div>
        }

        <!-- Interactive SVG Overlay Layer -->
        <svg
          class="absolute inset-0 h-full w-full pointer-events-auto"
          [attr.viewBox]="'0 0 ' + viewWidth() + ' ' + viewHeight()"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <!-- Watermark Pattern for subtle hatched fill -->
            <pattern id="watermark-hatch" width="8" height="8" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#ef4444" stroke-width="1.5" stroke-opacity="0.25" />
            </pattern>

            <pattern id="border-stripes" width="10" height="10" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
              <line x1="0" y1="0" x2="0" y2="10" stroke="#f59e0b" stroke-width="2" stroke-opacity="0.3" />
            </pattern>
          </defs>

          <!-- 1. Detected Borders Overlay (Letterbox / Pillarbox indicators) -->
          @for (b of borders(); track b) {
            @switch (b) {
              @case ('up') {
                <rect
                  x="0"
                  y="0"
                  [attr.width]="viewWidth()"
                  [attr.height]="borderThicknessY()"
                  fill="#000000"
                  fill-opacity="0.65"
                  stroke="#f59e0b"
                  stroke-dasharray="4 2"
                  stroke-width="1.5"
                />
                <text
                  x="12"
                  [attr.y]="borderThicknessY() - 8"
                  fill="#f59e0b"
                  font-size="14"
                  font-family="monospace"
                  font-weight="bold"
                >
                  &#9650; RAND OBEN ERKANNT
                </text>
              }
              @case ('down') {
                <rect
                  x="0"
                  [attr.y]="viewHeight() - borderThicknessY()"
                  [attr.width]="viewWidth()"
                  [attr.height]="borderThicknessY()"
                  fill="#000000"
                  fill-opacity="0.65"
                  stroke="#f59e0b"
                  stroke-dasharray="4 2"
                  stroke-width="1.5"
                />
                <text
                  x="12"
                  [attr.y]="viewHeight() - 10"
                  fill="#f59e0b"
                  font-size="14"
                  font-family="monospace"
                  font-weight="bold"
                >
                  &#9660; RAND UNTEN ERKANNT
                </text>
              }
              @case ('left') {
                <rect
                  x="0"
                  y="0"
                  [attr.width]="borderThicknessX()"
                  [attr.height]="viewHeight()"
                  fill="#000000"
                  fill-opacity="0.65"
                  stroke="#f59e0b"
                  stroke-dasharray="4 2"
                  stroke-width="1.5"
                />
              }
              @case ('right') {
                <rect
                  [attr.x]="viewWidth() - borderThicknessX()"
                  y="0"
                  [attr.width]="borderThicknessX()"
                  [attr.height]="viewHeight()"
                  fill="#000000"
                  fill-opacity="0.65"
                  stroke="#f59e0b"
                  stroke-dasharray="4 2"
                  stroke-width="1.5"
                />
              }
            }
          }

          <!-- 2. Watermark Detection Boxes (Red dashed, translucent red fill, label) -->
          @for (wm of svgWatermarks(); track $index) {
            <g class="watermark-group">
              <rect
                [attr.x]="wm.x"
                [attr.y]="wm.y"
                [attr.width]="wm.width"
                [attr.height]="wm.height"
                fill="#ef4444"
                fill-opacity="0.22"
                stroke="#ef4444"
                stroke-width="2"
                stroke-dasharray="4 4"
                rx="4"
              />
              <!-- Watermark label tag badge -->
              <rect
                [attr.x]="wm.x"
                [attr.y]="wm.y > 24 ? wm.y - 22 : wm.y"
                [attr.width]="Math.max(80, (wm.label || 'WATERMARK').length * 9 + 16)"
                height="20"
                fill="#ef4444"
                rx="3"
              />
              <text
                [attr.x]="wm.x + 6"
                [attr.y]="wm.y > 24 ? wm.y - 8 : wm.y + 14"
                fill="#ffffff"
                font-size="11"
                font-family="monospace"
                font-weight="bold"
              >
                {{ wm.label || 'WATERMARK' }}
              </text>
            </g>
          }

          <!-- 3. Smart-Crop Proposed Box (Solid green border #22c55e, 2px, target bucket badge) -->
          @if (svgSmartCrop(); as crop) {
            <g class="smart-crop-group">
              <rect
                [attr.x]="crop.x"
                [attr.y]="crop.y"
                [attr.width]="crop.width"
                [attr.height]="crop.height"
                fill="none"
                stroke="#22c55e"
                stroke-width="2"
                rx="4"
              />

              <!-- Target Bucket badge in top-left of crop rectangle -->
              <g [attr.transform]="'translate(' + (crop.x + 6) + ',' + (crop.y + 6) + ')'">
                <rect
                  x="0"
                  y="0"
                  [attr.width]="cropBucketBadgeWidth()"
                  height="22"
                  fill="#22c55e"
                  rx="3"
                />
                <text
                  x="6"
                  y="15"
                  fill="#ffffff"
                  font-size="11"
                  font-family="monospace"
                  font-weight="bold"
                >
                  &#9986; {{ crop.targetBucket || 'SMART-CROP' }}
                </text>
              </g>
            </g>
          }
        </svg>
      </div>

      <!-- Footer Info -->
      <div class="flex flex-wrap items-center justify-between text-xs text-muted-foreground gap-2 pt-1">
        <div class="flex items-center gap-3">
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-full bg-emerald-500 inline-block"></span>
            <span>Grün: Vorgeschlagener Smart-Crop</span>
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-full bg-red-500 inline-block"></span>
            <span>Rot: Erkanntes Wasserzeichen</span>
          </span>
          @if (borders().length > 0) {
            <span class="inline-flex items-center gap-1.5">
              <span class="size-2.5 rounded-full bg-amber-500 inline-block"></span>
              <span>Gelb: Ränder ({{ borders().join(', ') }})</span>
            </span>
          }
        </div>

        <div class="flex items-center gap-2 font-mono text-[11px]">
          <span class="text-muted-foreground/80">Shortcuts:</span>
          <kbd class="rounded border border-border bg-muted px-1.5 py-0.5">C</kbd>
          <span class="text-muted-foreground/80">Crop anwenden</span>
          <kbd class="rounded border border-border bg-muted px-1.5 py-0.5">Shift+C</kbd>
          <span class="text-muted-foreground/80">Auf alle</span>
        </div>
      </div>
    </div>
  `,
})
export class WatermarkCropOverlayComponent {
  protected readonly Math = Math;

  readonly imageUrl = input<string | null>(null);
  readonly imageWidth = input<number>(1920);
  readonly imageHeight = input<number>(1080);
  readonly watermarks = input<WatermarkBox[]>([]);
  readonly smartCrop = input<SmartCropBox | null>(null);
  readonly borders = input<BorderDirection[]>([]);

  readonly applyCrop = output<SmartCropBox>();
  readonly applyCropToAll = output<SmartCropBox>();

  readonly viewWidth = computed(() => this.imageWidth() || 1920);
  readonly viewHeight = computed(() => this.imageHeight() || 1080);

  readonly aspectRatio = computed(() => `${this.viewWidth()} / ${this.viewHeight()}`);

  readonly borderThicknessY = computed(() => Math.round(this.viewHeight() * 0.08));
  readonly borderThicknessX = computed(() => Math.round(this.viewWidth() * 0.08));

  readonly svgWatermarks = computed<SvgRect[]>(() => {
    const w = this.viewWidth();
    const h = this.viewHeight();
    return this.watermarks().map((box) => {
      const norm = normalizeToSvg(box, w, h);
      return {
        ...norm,
        label: box.label || 'WATERMARK',
      };
    });
  });

  readonly svgSmartCrop = computed<SvgRect | null>(() => {
    const crop = this.smartCrop();
    if (!crop) return null;
    const norm = normalizeToSvg(crop, this.viewWidth(), this.viewHeight());
    return {
      ...norm,
      targetBucket: crop.targetBucket || 'SMART-CROP',
    };
  });

  readonly cropBucketBadgeWidth = computed(() => {
    const text = this.svgSmartCrop()?.targetBucket || 'SMART-CROP';
    return text.length * 8 + 24;
  });

  @HostListener('window:keydown', ['$event'])
  handleKeyDown(event: KeyboardEvent): void {
    // Avoid triggering when focused inside an input or textarea
    const target = event.target as HTMLElement | null;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
      return;
    }

    if (event.key === 'c' || event.key === 'C') {
      const crop = this.smartCrop();
      if (!crop) return;

      if (event.shiftKey) {
        event.preventDefault();
        this.onApplyCropToAll();
      } else {
        event.preventDefault();
        this.onApplyCrop();
      }
    }
  }

  onApplyCrop(): void {
    const crop = this.smartCrop();
    if (crop) {
      this.applyCrop.emit(crop);
    }
  }

  onApplyCropToAll(): void {
    const crop = this.smartCrop();
    if (crop) {
      this.applyCropToAll.emit(crop);
    }
  }
}
