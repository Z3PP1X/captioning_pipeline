import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideZap, lucideCheck, lucideX, lucideEye, lucideRefreshCw, lucideAlertTriangle, lucideTarget } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';

@Component({
  selector: 'app-curate-toolbar',
  imports: [NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideZap, lucideCheck, lucideX, lucideEye, lucideRefreshCw, lucideAlertTriangle, lucideTarget })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-3 shadow-xs select-none">
      <!-- Left: Title & Pick Badge -->
      <div class="flex items-center gap-2">
        <h2 class="font-sans font-bold text-sm text-foreground">
          Curation Grid: <span class="text-primary">{{ videoService.currentVideo()?.name || '--' }}</span>
        </h2>
        <span class="rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
          {{ curateService.filteredCandidates().length }} frames
        </span>
      </div>

      <!-- Center: Filter Dropdowns -->
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <!-- Chunk Filter -->
        <select
          [value]="curateService.curateChunkFilter()"
          (change)="curateService.curateChunkFilter.set($any($event.target).value)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All Chunks</option>
          @for (chunk of chunkService.chunks(); track chunk.position) {
            <option [value]="chunk.id || chunk.position">Chunk #{{ chunk.position + 1 }}</option>
          }
        </select>

        <!-- Role Filter -->
        <select
          [value]="curateService.curateRoleFilter()"
          (change)="curateService.curateRoleFilter.set($any($event.target).value)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All Roles</option>
          <option value="pick">300 Picks Only</option>
          <option value="runner_up">450 Runner-ups Only</option>
        </select>

        <!-- State Filter -->
        <select
          [value]="curateService.curateStateFilter()"
          (change)="curateService.curateStateFilter.set($any($event.target).value)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All States</option>
          <option value="keep">Keep / Selected</option>
          <option value="reject">Rejected</option>
          <option value="star">Starred</option>
        </select>

        <!-- Target Person Filter -->
        @if (videoService.currentVideo()?.target_person_name; as tName) {
          <button
            hlmBtn
            [variant]="curateService.targetPersonOnly() ? 'default' : 'outline'"
            size="xs"
            (click)="curateService.targetPersonOnly.set(!curateService.targetPersonOnly())"
            [title]="'Filter frames matching target person: ' + tName"
            class="h-8 cursor-pointer gap-1"
          >
            <span>🎯 {{ tName }}</span>
          </button>
        }

        <!-- Manage Target Person -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="openPersonModal.emit()"
          [title]="videoService.currentVideo()?.target_person_name ? 'Change target person' : 'Assign target person'"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideTarget" class="size-3.5" />
          <span>{{ videoService.currentVideo()?.target_person_name ? 'Person' : 'Target Person' }}</span>
        </button>

        <!-- Toggle Candidate Pool -->
        <button
          hlmBtn
          [variant]="curateService.showRunnerUps() ? 'secondary' : 'outline'"
          size="xs"
          (click)="toggleCandidatePool()"
          title="Toggle viewing all 450 candidate pool frames"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideEye" class="size-3.5" />
          <span>{{ curateService.showRunnerUps() ? 'Hide Pool' : 'View Pool (450)' }}</span>
        </button>

        <!-- Bulk Keep All Picks -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="confirmAction.set('keep_all')"
          [disabled]="!videoService.currentVideo() || curateService.candidates().length === 0"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideCheck" class="size-3.5" />
          <span>Keep All Picks</span>
        </button>

        <!-- Bulk Reject All -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="confirmAction.set('reject_all')"
          [disabled]="!videoService.currentVideo() || curateService.candidates().length === 0"
          class="h-8 cursor-pointer gap-1 text-muted-foreground hover:text-destructive"
        >
          <ng-icon name="lucideX" class="size-3.5" />
          <span>Reject All</span>
        </button>

        <!-- Sync Captions with Final Folder -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="onSyncCaptions()"
          [disabled]="videoService.isLoading()"
          title="Scan and sync captions from final folder"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideRefreshCw" class="size-3.5" [class.animate-spin]="videoService.isLoading()" />
          <span>Sync Captions</span>
        </button>

        <!-- Similarity Dedup 95% (Postprocessing Step) -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="onDedupSimilarity()"
          [disabled]="curateService.isLoading() || !videoService.currentVideo() || curateService.candidates().length === 0"
          title="Analyze similarity, keep best-scoring frames for pairs >= 95%, and trim active picks to 150-200"
          class="h-8 cursor-pointer gap-1 text-primary hover:text-primary"
        >
          <ng-icon name="lucideZap" class="size-3.5" [class.animate-spin]="curateService.isLoading()" />
          <span>Dedup 95% (150-200)</span>
        </button>
      </div>

      <!-- Right: Open VLM Captioning Modal -->
      <div class="flex flex-col items-end">
        <button
          hlmBtn
          variant="default"
          size="sm"
          (click)="openVlmModal.emit()"
          [disabled]="!videoService.currentVideo() || curateService.candidates().length === 0"
          aria-describedby="vlm-disabled-reason"
          title="Launch OpenRouter 3-layer VLM cascade captioning"
          class="cursor-pointer gap-1.5 font-bold"
        >
          <ng-icon name="lucideZap" class="size-3.5" />
          <span>VLM Captioning (Cascade)</span>
        </button>
        @if (!videoService.currentVideo()) {
          <span id="vlm-disabled-reason" class="text-xs text-muted-foreground mt-0.5">Select a video first</span>
        } @else if (curateService.candidates().length === 0) {
          <span id="vlm-disabled-reason" class="text-xs text-muted-foreground mt-0.5">Extract candidates before captioning</span>
        }
      </div>
    </div>

    <!-- Bulk confirmation dialog -->
    @if (confirmAction()) {
      <div class="fixed inset-0 z-50 flex items-center justify-center bg-secondary/80 backdrop-blur-xs p-4 animate-in fade-in select-none">
        <div class="relative w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-2xl flex flex-col gap-4">
          <div class="flex items-center gap-2">
            <ng-icon
              [name]="confirmAction() === 'reject_all' ? 'lucideAlertTriangle' : 'lucideCheck'"
              [class]="confirmAction() === 'reject_all' ? 'size-5 text-destructive' : 'size-5 text-primary'"
            />
            <h3 class="font-sans font-bold text-base text-foreground">
              {{ confirmAction() === 'reject_all' ? 'Reject All Candidates?' : 'Keep All Picks?' }}
            </h3>
          </div>

          <p class="text-xs text-muted-foreground leading-relaxed">
            @if (confirmAction() === 'reject_all') {
              Are you sure you want to mark all candidates for this video as rejected? You will need to manually re-select or re-extract candidates.
            } @else {
              Set all curated picks in this video to "Keep"? This accepts all 300 target frames into the dataset.
            }
          </p>

          <div class="flex items-center justify-end gap-2 pt-2 border-t border-border">
            <button
              hlmBtn
              variant="outline"
              size="sm"
              (click)="confirmAction.set(null)"
              class="cursor-pointer"
            >
              Cancel
            </button>
            <button
              hlmBtn
              [variant]="confirmAction() === 'reject_all' ? 'destructive' : 'default'"
              size="sm"
              (click)="executeConfirmAction()"
              class="cursor-pointer font-bold"
            >
              {{ confirmAction() === 'reject_all' ? 'Reject All' : 'Confirm Keep All' }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
})
export class CurateToolbarComponent {
  readonly curateService = inject(CurateService);
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  readonly openVlmModal = output<void>();
  readonly openPersonModal = output<void>();
  readonly confirmAction = signal<'keep_all' | 'reject_all' | null>(null);

  toggleCandidatePool(): void {
    this.curateService.showRunnerUps.update((v) => !v);
  }

  async onSyncCaptions(): Promise<void> {
    const video = this.videoService.currentVideo();
    await this.videoService.syncCaptions();
    if (video) {
      await this.curateService.loadCandidates(video.id);
    }
  }

  async onDedupSimilarity(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;
    await this.curateService.deduplicateSimilarity(video.id);
  }

  async executeConfirmAction(): Promise<void> {
    const action = this.confirmAction();
    const video = this.videoService.currentVideo();
    this.confirmAction.set(null);
    if (!video) return;

    if (action === 'keep_all') {
      await this.curateService.bulkKeepAll(video.id);
    } else if (action === 'reject_all') {
      await this.curateService.bulkRejectAll(video.id);
    }
  }
}
