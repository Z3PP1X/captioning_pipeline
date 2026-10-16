import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input, output, signal } from '@angular/core';
import { TagBox } from '../../core/models/tag.model';

export interface OverlayBox {
  key: string;
  tagId: number;
  label: string;
  color: string | null;
  box: TagBox;
  kind: 'confirmed' | 'suggested' | 'negative';
  score?: number | null;
}

const MIN_SIZE = 0.02;

/**
 * Sits exactly on top of the lightbox image. Shows marked regions (solid), suggestions (dashed) and, in draw
 * mode, lets the user drag a box. Coordinates are normalised to the image (0-1).
 */
@Component({
  selector: 'app-region-overlay',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'absolute inset-0 block',
    '[class.cursor-crosshair]': 'drawMode()',
    '[class.pointer-events-none]': '!drawMode()',
    '(pointerdown)': 'onPointerDown($event)',
    '(pointermove)': 'onPointerMove($event)',
    '(pointerup)': 'onPointerUp($event)',
    '(pointercancel)': 'draft.set(null)',
    '[attr.aria-hidden]': 'true',
  },
  template: `
    @for (b of boxes(); track b.key) {
      <div
        class="absolute rounded-sm border-2 transition-opacity"
        [class.border-dashed]="b.kind === 'suggested'"
        [class.border-dotted]="b.kind === 'negative'"
        [class.opacity-40]="dimmed(b)"
        [style.left.%]="b.box.x * 100"
        [style.top.%]="b.box.y * 100"
        [style.width.%]="b.box.w * 100"
        [style.height.%]="b.box.h * 100"
        [style.border-color]="b.color || 'var(--primary)'"
      >
        <span
          class="absolute -top-5 left-0 max-w-48 truncate rounded-sm px-1 py-px text-xs font-semibold leading-4 text-primary-foreground shadow-sm"
          [style.background-color]="b.color || 'var(--primary)'"
        >
          {{ b.kind === 'negative' ? 'not ' : '' }}{{ b.label }}{{ b.score != null && b.kind === 'suggested' ? ' ' + pct(b.score) : '' }}
        </span>
      </div>
    }

    @if (liveBox(); as d) {
      <div
        class="absolute rounded-sm border-2 border-primary bg-primary/15 ring-1 ring-primary-foreground/70"
        [style.left.%]="d.x * 100"
        [style.top.%]="d.y * 100"
        [style.width.%]="d.w * 100"
        [style.height.%]="d.h * 100"
      ></div>
    }
  `,
})
export class RegionOverlayComponent {
  private readonly el = inject(ElementRef<HTMLElement>);

  readonly boxes = input<OverlayBox[]>([]);
  readonly drawMode = input(false);
  readonly pendingBox = input<TagBox | null>(null);
  readonly highlightTagId = input<number | null>(null);
  readonly boxDrawn = output<TagBox>();

  readonly draft = signal<TagBox | null>(null);
  private start: { x: number; y: number } | null = null;

  /** The box being dragged, else the drawn box waiting for a name. */
  readonly liveBox = computed(() => this.draft() ?? this.pendingBox());

  dimmed(b: OverlayBox): boolean {
    const h = this.highlightTagId();
    return h !== null && h !== b.tagId;
  }

  pct(score: number): string {
    return `${Math.round(score * 100)}%`;
  }

  private point(e: PointerEvent): { x: number; y: number } {
    const rect = (this.el.nativeElement as HTMLElement).getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / Math.max(1, rect.width))),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / Math.max(1, rect.height))),
    };
  }

  onPointerDown(e: PointerEvent): void {
    if (!this.drawMode() || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    (this.el.nativeElement as HTMLElement).setPointerCapture?.(e.pointerId);
    this.start = this.point(e);
    this.draft.set({ ...this.start, w: 0, h: 0 });
  }

  onPointerMove(e: PointerEvent): void {
    if (!this.start) return;
    const p = this.point(e);
    this.draft.set({
      x: Math.min(this.start.x, p.x),
      y: Math.min(this.start.y, p.y),
      w: Math.abs(p.x - this.start.x),
      h: Math.abs(p.y - this.start.y),
    });
  }

  onPointerUp(e: PointerEvent): void {
    if (!this.start) return;
    e.stopPropagation();
    const box = this.draft();
    this.start = null;
    this.draft.set(null);
    if (box && box.w >= MIN_SIZE && box.h >= MIN_SIZE) {
      this.boxDrawn.emit(box);
    }
  }
}
