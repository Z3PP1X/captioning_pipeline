import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideZap, lucideCheck, lucideX, lucideEye, lucideRefreshCw } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';

@Component({
  selector: 'app-curate-toolbar',
  imports: [FormsModule, NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideZap, lucideCheck, lucideX, lucideEye, lucideRefreshCw })],
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
          [ngModel]="curateService.curateChunkFilter()"
          (ngModelChange)="curateService.curateChunkFilter.set($event)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All Chunks</option>
          @for (chunk of chunkService.chunks(); track chunk.position) {
            <option [value]="chunk.id || chunk.position">Chunk #{{ chunk.position + 1 }}</option>
          }
        </select>

        <!-- Role Filter -->
        <select
          [ngModel]="curateService.curateRoleFilter()"
          (ngModelChange)="curateService.curateRoleFilter.set($event)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All Roles</option>
          <option value="pick">300 Picks Only</option>
          <option value="runner_up">450 Runner-ups Only</option>
        </select>

        <!-- State Filter -->
        <select
          [ngModel]="curateService.curateStateFilter()"
          (ngModelChange)="curateService.curateStateFilter.set($event)"
          class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
        >
          <option value="all">All States</option>
          <option value="keep">Keep / Selected</option>
          <option value="reject">Rejected</option>
          <option value="star">Starred</option>
        </select>

        <!-- Toggle Candidate Pool -->
        <button
          hlmBtn
          [variant]="curateService.showRunnerUps() ? 'default' : 'outline'"
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
          (click)="onKeepAll()"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideCheck" class="size-3.5 text-success" />
          <span>Keep All Picks</span>
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
      </div>

      <!-- Right: Open VLM Captioning Modal -->
      <div>
        <button
          hlmBtn
          variant="default"
          size="sm"
          (click)="openVlmModal.emit()"
          [disabled]="!videoService.currentVideo() || curateService.candidates().length === 0"
          title="Launch OpenRouter 3-layer VLM cascade captioning"
          class="cursor-pointer gap-1.5 font-bold"
        >
          <ng-icon name="lucideZap" class="size-3.5" />
          <span>VLM Captioning (Cascade)</span>
        </button>
      </div>
    </div>
  `,
})
export class CurateToolbarComponent {
  readonly curateService = inject(CurateService);
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  readonly openVlmModal = output<void>();

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

  async onKeepAll(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      await this.curateService.bulkKeepAll(video.id);
    }
  }

  async onRejectAll(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (video) {
      await this.curateService.bulkRejectAll(video.id);
    }
  }
}
