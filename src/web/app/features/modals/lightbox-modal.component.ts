import { ChangeDetectionStrategy, Component, computed, effect, inject, output } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideX,
  lucideStar,
  lucideChevronLeft,
  lucideChevronRight,
  lucideTarget,
  lucideUserPlus,
  lucideTags,
  lucideSparkles,
  lucideQuote,
  lucideScanSearch,
  lucideLoader2,
} from '@ng-icons/lucide';
import { PromptRagService } from '../../core/services/prompt-rag.service';
import { JobEventService } from '../../core/services/job-event.service';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { formatTime } from '../../core/utils/time.utils';
import { candidateChunkInfo } from '../../core/utils/chunk-fields.utils';
import { CandidateState, CandidateFrame } from '../../core/models/candidate.model';
import { TagService } from '../../core/services/tag.service';
import { TagBox, TagLearnResponse } from '../../core/models/tag.model';
import { RegionOverlayComponent, OverlayBox } from '../tags/region-overlay.component';
import { FrameTagsPanelComponent } from '../tags/frame-tags-panel.component';

@Component({
  selector: 'app-lightbox-modal',
  imports: [CdkTrapFocus, NgIcon, HlmButtonImports, HlmBadgeImports, RegionOverlayComponent, FrameTagsPanelComponent],
  providers: [
    provideIcons({
      lucideCheck,
      lucideX,
      lucideStar,
      lucideChevronLeft,
      lucideChevronRight,
      lucideTarget,
      lucideUserPlus,
      lucideTags,
      lucideSparkles,
      lucideQuote,
      lucideScanSearch,
      lucideLoader2,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (candidate(); as c) {
      <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-secondary/80 backdrop-blur-xs p-4 animate-in fade-in select-none"
        tabindex="-1"
        (click)="onBackdropClick($event)"
        (keydown.escape)="curateService.closeLightbox()"
      >
        <!-- Custom media surface (not hlm-dialog): keeps dialog semantics and traps focus while open;
             focus starts on the image area and returns to the opener (grid card) when the lightbox closes -->
        <div
          class="relative flex flex-col lg:flex-row max-h-[90vh] max-w-6xl w-full rounded-xl border border-border bg-card overflow-hidden shadow-2xl"
          role="dialog"
          aria-modal="true"
          aria-label="Image Lightbox Preview"
          tabindex="-1"
          cdkTrapFocus
          [cdkTrapFocusAutoCapture]="true"
          (click)="$event.stopPropagation()"
          (keydown.escape)="curateService.closeLightbox()"
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
          <div
            class="relative flex-1 bg-secondary flex items-center justify-center min-h-[300px] lg:min-h-[500px] outline-none"
            tabindex="-1"
            cdkFocusInitial
          >
            <!-- The wrapper is exactly the size of the rendered image, so overlay boxes line up with it -->
            <div class="relative inline-block max-h-[85vh] max-w-full">
              <img
                [src]="getFullUrl(c)"
                [alt]="'Full resolution frame at ' + formatTime(c.time_s)"
                class="block max-h-[85vh] max-w-full object-contain"
                draggable="false"
              />
              <app-region-overlay
                [boxes]="overlayBoxes()"
                [drawMode]="tagService.drawMode()"
                [pendingBox]="tagService.pendingBox()"
                [highlightTagId]="tagService.highlightTagId()"
                (boxDrawn)="onBoxDrawn($event)"
              />
            </div>

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
                  @let chunkPos = chunkInfo(c).position;
                  Chunk #{{ chunkPos !== null ? chunkPos + 1 : c.chunk_id }}
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
                [class.ring-warning-strong]="c.state === 'star'"
              >
                <ng-icon name="lucideStar" class="size-3.5 text-warning-strong" />
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
                  <span hlmBadge variant="secondary" class="gap-1 bg-primary/20 text-primary border border-primary/30 text-xs font-bold">
                    <ng-icon name="lucideTarget" class="size-3" />
                    Match ({{ Math.round((c.target_person_sim || 0) * 100) }}%)
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

            <!-- Learn-as-you-tag image tags -->
            <app-frame-tags-panel [frameId]="c.id" (changed)="onTagsChanged(c, $event)" />

            <!-- Prompt Director links: caption as description seed, or image-similar exemplars -->
            <div class="flex flex-col gap-2 py-3 border-b border-border" role="group" aria-labelledby="lb-prompt-title">
              <span id="lb-prompt-title" class="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                <ng-icon name="lucideSparkles" class="size-3.5" aria-hidden="true" />
                Prompt Director
              </span>
              <div class="grid grid-cols-2 gap-2">
                <button
                  hlmBtn
                  variant="outline"
                  size="sm"
                  type="button"
                  (click)="onUseAsSeed(c)"
                  [disabled]="rag.isSeeding()"
                  aria-describedby="lb-seed-help"
                >
                  <ng-icon [name]="rag.isSeeding() ? 'lucideLoader2' : 'lucideQuote'" class="size-3.5" [class.animate-spin]="rag.isSeeding()" aria-hidden="true" />
                  Use as prompt seed
                </button>
                <button hlmBtn variant="outline" size="sm" type="button" (click)="onMoreLikeThis(c)">
                  <ng-icon name="lucideScanSearch" class="size-3.5" aria-hidden="true" />
                  More like this
                </button>
              </div>
              <span id="lb-seed-help" class="text-xs text-muted-foreground">
                Opens the Prompt Director with this frame's caption as the description.
              </span>
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
  readonly chunkInfo = candidateChunkInfo;
  readonly videoService = inject(VideoService);
  readonly candidate = this.curateService.currentLightboxCandidate;

  readonly tagService = inject(TagService);

  readonly tagPerson = output<number>();

  readonly rag = inject(PromptRagService);
  private readonly jobEvents = inject(JobEventService);

  private seedLabel(c: CandidateFrame): string {
    const name = this.videoService.currentVideo()?.name;
    return [name, formatTime(c.time_s)].filter(Boolean).join(' · ');
  }

  async onUseAsSeed(c: CandidateFrame): Promise<void> {
    // The lightbox is an app-level overlay: close it on success, or it would stay on top of the Prompt Director
    const error = await this.rag.useFrameAsSeed(c.id, this.seedLabel(c));
    if (error) {
      this.jobEvents.showToast('No prompt seed', error, null, true);
    } else {
      this.curateService.closeLightbox();
    }
  }

  onMoreLikeThis(c: CandidateFrame): void {
    this.curateService.closeLightbox();
    this.rag.showSimilarTo(c.id, this.seedLabel(c));
  }

  /** Marked regions (solid), negative examples (dotted) and suggestions (dashed) of the open frame. */
  readonly overlayBoxes = computed<OverlayBox[]>(() => {
    const f = this.tagService.frameTags();
    const c = this.candidate();
    if (!f || !c || f.frame_id !== c.id) return [];
    const boxes: OverlayBox[] = f.regions.map((r) => ({
      key: `r${r.id}`,
      tagId: r.tag_id,
      label: r.name || '',
      color: r.color ?? null,
      box: r.box,
      kind: r.polarity === 'negative' ? 'negative' : 'confirmed',
    }));
    for (const s of f.suggestions) {
      if (s.box) {
        boxes.push({ key: `s${s.tag_id}`, tagId: s.tag_id, label: s.name, color: s.color, box: s.box, kind: 'suggested', score: s.score });
      }
    }
    return boxes;
  });

  constructor() {
    // Load tags for whichever frame the lightbox shows (and reset the marking state on navigation)
    effect(() => {
      const c = this.candidate();
      this.tagService.setCurrentFrame(c ? c.id : null);
    });
    this.tagService.loadTags().catch(() => undefined);
  }

  onBoxDrawn(box: TagBox): void {
    this.tagService.pendingBox.set(box);
  }

  /** Mirror the frame's new tags into the curation grid without a refetch. */
  onTagsChanged(c: CandidateFrame, res: TagLearnResponse): void {
    const f = res.frame;
    if (!f) return;
    this.curateService.setFrameTags(c.id, [
      ...f.confirmed.map((t) => ({ tag_id: t.tag_id, name: t.name, color: t.color, status: 'confirmed' as const, score: t.score })),
      ...f.suggestions.map((t) => ({ tag_id: t.tag_id, name: t.name, color: t.color, status: 'suggested' as const, score: t.score })),
    ]);
  }
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
