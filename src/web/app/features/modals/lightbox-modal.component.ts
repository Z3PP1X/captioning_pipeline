import { ChangeDetectionStrategy, Component, computed, inject, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideX,
  lucideStar,
  lucideChevronLeft,
  lucideChevronRight,
  lucideTarget,
  lucideUserPlus,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { formatTime } from '../../core/utils/time.utils';
import { CandidateState } from '../../core/models/candidate.model';

@Component({
  selector: 'app-lightbox-modal',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideCheck,
      lucideX,
      lucideStar,
      lucideChevronLeft,
      lucideChevronRight,
      lucideTarget,
      lucideUserPlus,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (candidate(); as c) {
      <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 animate-in fade-in select-none"
        role="dialog"
        aria-modal="true"
        aria-label="Image Lightbox Preview"
        (click)="onBackdropClick($event)"
      >
        <div
          class="relative flex flex-col lg:flex-row max-h-[90vh] max-w-6xl w-full rounded-xl border border-border bg-card overflow-hidden shadow-2xl"
          (click)="$event.stopPropagation()"
        >
          <!-- Close Button -->
          <button
            (click)="curateService.closeLightbox()"
            class="absolute top-3 right-3 z-20 flex size-8 items-center justify-center rounded-full bg-secondary/80 text-secondary-foreground hover:bg-secondary transition-all cursor-pointer"
            title="Close Lightbox (Escape)"
            aria-label="Close Lightbox"
          >
            <ng-icon name="lucideX" class="size-4" />
          </button>

          <!-- Main Image View & Prev/Next Arrows -->
          <div class="relative flex-1 bg-black flex items-center justify-center min-h-[300px] lg:min-h-[500px]">
            <img
              [src]="getFullUrl(c)"
              [alt]="'Full resolution frame at ' + formatTime(c.time_s)"
              class="max-h-[85vh] max-w-full object-contain"
            />

            <!-- Prev Arrow -->
            <button
              (click)="curateService.prevLightbox()"
              class="absolute left-3 top-1/2 -translate-y-1/2 flex size-10 items-center justify-center rounded-full bg-secondary/80 text-secondary-foreground hover:bg-secondary transition-all cursor-pointer shadow-md"
              title="Previous frame (Left arrow)"
              aria-label="Previous frame"
            >
              <ng-icon name="lucideChevronLeft" class="size-6" />
            </button>

            <!-- Next Arrow -->
            <button
              (click)="curateService.nextLightbox()"
              class="absolute right-3 top-1/2 -translate-y-1/2 flex size-10 items-center justify-center rounded-full bg-secondary/80 text-secondary-foreground hover:bg-secondary transition-all cursor-pointer shadow-md"
              title="Next frame (Right arrow)"
              aria-label="Next frame"
            >
              <ng-icon name="lucideChevronRight" class="size-6" />
            </button>
          </div>

          <!-- Sidebar: Timecode, Badges, Actions, 7-Dimension Scores -->
          <div class="flex flex-col w-full lg:w-80 p-5 border-t lg:border-t-0 lg:border-l border-border bg-card overflow-y-auto">
            <!-- Timecode & Status -->
            <div class="flex items-center justify-between border-b border-border pb-3">
              <div>
                <h4 class="text-base font-bold text-foreground tabular-nums">
                  {{ formatTime(c.time_s) }}
                </h4>
                <span class="text-xs text-muted-foreground">
                  Chunk #{{ (c.chunk_position !== undefined ? c.chunk_position + 1 : c.chunk_id) }}
                </span>
              </div>
              <span
                class="px-2 py-0.5 rounded-md text-xs font-bold uppercase tracking-wide"
                [class]="c.is_pick ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'"
              >
                {{ c.is_pick ? 'Pick' : 'Runner' }}
              </span>
            </div>

            <!-- Actions: Keep, Reject, Star -->
            <div class="flex items-center justify-between gap-2 py-4 border-b border-border">
              <button
                hlmBtn
                [variant]="c.state === 'keep' ? 'default' : 'outline'"
                size="sm"
                (click)="onSetState('keep')"
                aria-label="Keep frame (Hotkey K)"
                class="flex-1 cursor-pointer gap-1 font-semibold"
              >
                <ng-icon name="lucideCheck" class="size-3.5" />
                <span>Keep <kbd class="text-xs">K</kbd></span>
              </button>

              <button
                hlmBtn
                [variant]="c.state === 'star' ? 'secondary' : 'outline'"
                size="sm"
                (click)="onSetState('star')"
                aria-label="Star frame (Hotkey S)"
                class="flex-1 cursor-pointer gap-1 font-semibold"
                [class.ring-1]="c.state === 'star'"
                [class.ring-warning]="c.state === 'star'"
              >
                <ng-icon name="lucideStar" class="size-3.5 text-warning" />
                <span>Star <kbd class="text-xs">S</kbd></span>
              </button>

              <button
                hlmBtn
                [variant]="c.state === 'reject' ? 'destructive' : 'outline'"
                size="sm"
                (click)="onSetState('reject')"
                aria-label="Reject frame (Hotkey X)"
                class="flex-1 cursor-pointer gap-1 font-semibold"
              >
                <ng-icon name="lucideX" class="size-3.5" />
                <span>Reject <kbd class="text-xs">X</kbd></span>
              </button>
            </div>

            <!-- Face Recognition & Target Person Match Section -->
            <div class="flex flex-col gap-2 py-3.5 border-b border-border">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                  <ng-icon name="lucideTarget" class="size-3.5 text-primary" />
                  <span>Face Recognition</span>
                </span>
                @if (c.target_person_matched || (c.target_person_sim && c.target_person_sim >= 0.363)) {
                  <span hlmBadge variant="secondary" class="bg-primary/20 text-primary border border-primary/30 text-[10px] font-bold">
                    🎯 Match ({{ Math.round((c.target_person_sim || 0) * 100) }}%)
                  </span>
                }
              </div>

              @if (c.face_count !== undefined && c.face_count > 0) {
                <div class="flex items-center justify-between text-xs py-0.5">
                  <span class="text-muted-foreground">Detected Faces:</span>
                  <span class="font-bold text-foreground">{{ c.face_count }}</span>
                </div>
              }

              @if (videoService.currentVideo()?.target_person_name; as tName) {
                <div class="flex items-center justify-between text-xs py-0.5">
                  <span class="text-muted-foreground">Target Person:</span>
                  <span class="font-bold text-primary">{{ tName }}</span>
                </div>
                <div class="flex items-center justify-between text-xs py-0.5">
                  <span class="text-muted-foreground">Identity Similarity:</span>
                  <span class="tabular-nums font-bold text-foreground">
                    {{ ((c.target_person_sim || 0) * 100).toFixed(1) }}%
                  </span>
                </div>
              }

              <!-- Tag Person Action Button -->
              <button
                hlmBtn
                variant="outline"
                size="sm"
                (click)="tagPerson.emit(c.id)"
                class="w-full mt-1.5 cursor-pointer gap-1.5 font-semibold text-xs border-primary/40 text-primary hover:bg-primary/10 transition-colors"
                title="Tag the face detected in this frame to build/update the person identity"
              >
                <ng-icon name="lucideUserPlus" class="size-3.5" />
                <span>Tag Face as Person</span>
              </button>
            </div>

            <!-- 7-Dimension Scoring Breakdown -->
            <div class="flex flex-col gap-2.5 pt-4">
              <h5 class="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                7-Dimension Scoring Breakdown
              </h5>

              <div class="space-y-2 text-xs">
                @let m = c.metrics;
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground font-medium">Combined Score:</span>
                  <span class="tabular-nums font-bold text-primary">{{ ((c.score || 0) * 100).toFixed(1) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground">Blur Laplacian:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ (m?.blur_laplacian || 0).toFixed(1) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground">Color Entropy:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ (m?.color_entropy || 0).toFixed(2) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground">Edge Density:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ (m?.edge_density || 0).toFixed(3) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground">Face Score:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ ((m?.face_score || 0) * 100).toFixed(1) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5 border-b border-border/40">
                  <span class="text-muted-foreground">Person Score:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ ((m?.person_score || 0) * 100).toFixed(1) }}</span>
                </div>
                <div class="flex justify-between items-center py-0.5">
                  <span class="text-muted-foreground">Equipment Score:</span>
                  <span class="tabular-nums font-medium text-foreground">{{ ((m?.equipment_score || 0) * 100).toFixed(1) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    }
  `,
})
export class LightboxModalComponent {
  readonly curateService = inject(CurateService);
  readonly videoService = inject(VideoService);
  readonly candidate = this.curateService.currentLightboxCandidate;

  readonly tagPerson = output<number>();
  readonly formatTime = formatTime;
  readonly Math = Math;

  getFullUrl(c: { video_id?: string | number; id: number; full_url?: string }): string {
    if (c.full_url) return c.full_url;
    return `/api/frames/${c.id}/full`;
  }

  onBackdropClick(e: MouseEvent): void {
    if (e.target === e.currentTarget) {
      this.curateService.closeLightbox();
    }
  }

  onSetState(state: CandidateState): void {
    const c = this.candidate();
    if (c) {
      const nextState = c.state === state ? null : state;
      this.curateService.setCandidateState(c.video_id, c.id, nextState);
    }
  }
}
