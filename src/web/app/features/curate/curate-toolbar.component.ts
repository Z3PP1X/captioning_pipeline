import { ChangeDetectionStrategy, Component, OnInit, inject, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideZap,
  lucideCheck,
  lucideX,
  lucideEye,
  lucideRefreshCw,
  lucideAlertTriangle,
  lucideTarget,
  lucideTags,
  lucideSparkles,
  lucideDownload,
  lucideLoader2,
  lucideShuffle,
} from '@ng-icons/lucide';
import { ReselectDialogComponent } from './reselect-dialog.component';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { TagService } from '../../core/services/tag.service';
import { JobEventService } from '../../core/services/job-event.service';
import { ApiError } from '../../core/services/api.service';
import { DatasetService } from '../../core/services/dataset.service';
import { ConfirmService } from '../../core/services/confirm.service';

@Component({
  selector: 'app-curate-toolbar',
  imports: [NgIcon, HlmButtonImports, HlmNativeSelectImports, ReselectDialogComponent],
  providers: [
    provideIcons({
      lucideZap,
      lucideCheck,
      lucideX,
      lucideEye,
      lucideRefreshCw,
      lucideAlertTriangle,
      lucideTarget,
      lucideTags,
      lucideSparkles,
      lucideDownload,
      lucideLoader2,
      lucideShuffle,
    }),
  ],
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
        <label for="curate-chunk-filter" class="sr-only">Filter by chunk</label>
        <hlm-native-select
          selectId="curate-chunk-filter"
          selectClass="text-xs"
          size="sm"
          [value]="'' + curateService.curateChunkFilter()"
          (valueChange)="curateService.curateChunkFilter.set($any($event || 'all'))"
        >
          <option value="all">All Chunks</option>
          @for (chunk of chunkService.chunks(); track chunk.position) {
            <option [value]="'' + (chunk.id || chunk.position)">Chunk #{{ chunk.position + 1 }}</option>
          }
        </hlm-native-select>

        <!-- Role Filter -->
        <label for="curate-role-filter" class="sr-only">Filter by role</label>
        <hlm-native-select
          selectId="curate-role-filter"
          selectClass="text-xs"
          size="sm"
          [value]="curateService.curateRoleFilter()"
          (valueChange)="curateService.curateRoleFilter.set($any($event || 'all'))"
        >
          <option value="all">All Roles</option>
          <option value="pick">300 Picks Only</option>
          <option value="runner_up">450 Runner-ups Only</option>
        </hlm-native-select>

        <!-- State Filter -->
        <label for="curate-state-filter" class="sr-only">Filter by state</label>
        <hlm-native-select
          selectId="curate-state-filter"
          selectClass="text-xs"
          size="sm"
          [value]="curateService.curateStateFilter()"
          (valueChange)="curateService.curateStateFilter.set($any($event || 'all'))"
        >
          <option value="all">All States</option>
          <option value="keep">Keep / Selected</option>
          <option value="reject">Rejected</option>
          <option value="star">Starred</option>
        </hlm-native-select>

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
            <ng-icon name="lucideTarget" class="size-3.5" />
            <span>{{ tName }}</span>
          </button>
        }

        <!-- Image Tag Filter -->
        <label for="curate-tag-filter" class="sr-only">Filter by image tag</label>
        <hlm-native-select
          selectId="curate-tag-filter"
          selectClass="text-xs"
          size="sm"
          [value]="'' + (curateService.tagFilter() ?? '')"
          (valueChange)="onTagFilter($event ?? '')"
        >
          <option value="">All Tags</option>
          @for (t of tagService.tags(); track t.id) {
            <option [value]="'' + t.id">{{ t.name }}</option>
          }
        </hlm-native-select>
        @if (curateService.tagFilter() !== null) {
          <button
            hlmBtn
            size="xs"
            [variant]="curateService.tagFilterIncludeSuggested() ? 'outline' : 'secondary'"
            (click)="curateService.tagFilterIncludeSuggested.update((v) => !v)"
            class="h-8 cursor-pointer"
            [attr.aria-pressed]="!curateService.tagFilterIncludeSuggested()"
          >
            {{ curateService.tagFilterIncludeSuggested() ? 'Confirmed + suggested' : 'Confirmed only' }}
          </button>
        }

        <!-- Learn-as-you-tag: library and video-wide suggestions -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="openTagManager.emit()"
          title="Tag library: examples, learned accuracy, rename and merge"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideTags" class="size-3.5" />
          <span>Tags</span>
        </button>
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="onAutoTag()"
          [disabled]="autoTagging() || !videoService.currentVideo() || curateService.candidates().length === 0 || tagService.tags().length === 0"
          title="Suggest learned tags (with locations) on every frame of this video"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideSparkles" class="size-3.5" [class.animate-pulse]="autoTagging()" />
          <span>Auto-tag</span>
        </button>

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
          (click)="onKeepAll()"
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
          (click)="onRejectAll()"
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

        <!-- Phase F: reselect knobs + learned weights (opens app-reselect-dialog) -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          type="button"
          (click)="isReselectOpen.set(true)"
          [disabled]="curateService.isLoading() || !videoService.currentVideo() || curateService.candidates().length === 0"
          aria-haspopup="dialog"
          class="h-8 cursor-pointer gap-1"
        >
          <ng-icon name="lucideShuffle" class="size-3.5" aria-hidden="true" />
          <span>Reselect</span>
        </button>

        <!-- F13: export only this video (POST /api/export {video_id}) -->
        <button
          hlmBtn
          variant="outline"
          size="xs"
          (click)="onExportVideo()"
          [disabled]="isExporting() || !videoService.currentVideo() || curateService.candidates().length === 0"
          title="Export the kept frames of this video to the finished dataset"
          class="h-8 cursor-pointer gap-1"
        >
          @if (isExporting()) {
            <ng-icon name="lucideLoader2" class="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
          } @else {
            <ng-icon name="lucideDownload" class="size-3.5" aria-hidden="true" />
          }
          <span>Export this video</span>
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

    @if (isReselectOpen()) {
      <app-reselect-dialog (close)="isReselectOpen.set(false)" />
    }

  `,
})
export class CurateToolbarComponent implements OnInit {
  readonly curateService = inject(CurateService);
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);
  readonly tagService = inject(TagService);
  private readonly jobEvents = inject(JobEventService);
  private readonly datasetService = inject(DatasetService);
  private readonly confirm = inject(ConfirmService);

  readonly openVlmModal = output<void>();
  readonly openPersonModal = output<void>();
  readonly openTagManager = output<void>();
  readonly autoTagging = signal(false);
  readonly isExporting = signal(false);
  readonly isReselectOpen = signal(false);

  ngOnInit(): void {
    this.tagService.loadTags().catch(() => undefined);
  }

  onTagFilter(value: string): void {
    this.curateService.tagFilter.set(value ? Number(value) : null);
  }

  async onAutoTag(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;
    this.autoTagging.set(true);
    try {
      await this.tagService.suggestForVideo(video.id);
      this.jobEvents.showToast('Auto-tagging started', 'Suggestions appear in the grid when the job finishes.');
    } catch (err) {
      this.jobEvents.showToast('Auto-tagging failed', err instanceof ApiError ? err.message : String(err), null, true);
    } finally {
      this.autoTagging.set(false);
    }
  }

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

  async onKeepAll(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;
    const ok = await this.confirm.ask({
      title: 'Keep all picks?',
      description: 'Set all curated picks in this video to "Keep". This accepts all target frames into the dataset.',
      confirmLabel: 'Keep all',
      destructive: false,
    });
    if (ok) await this.curateService.bulkKeepAll(video.id);
  }

  async onRejectAll(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;
    const ok = await this.confirm.ask({
      title: 'Reject all candidates?',
      description:
        'All candidates of this video are marked as rejected. You will need to re-select frames manually or re-extract.',
      confirmLabel: 'Reject all',
    });
    if (ok) await this.curateService.bulkRejectAll(video.id);
  }

  async onExportVideo(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video || this.isExporting()) return;
    this.isExporting.set(true);
    try {
      const res = await this.datasetService.exportVideo(video.id);
      if (res?.error) {
        this.jobEvents.showToast('Export failed', res.error, video.id, true);
      } else if (!res?.exported) {
        this.jobEvents.showToast('Nothing exported', res?.msg || 'No frames are marked Keep or Star yet.', video.id);
      } else {
        const where = res.per_video_dir ? ` to ${res.per_video_dir}` : '';
        this.jobEvents.showToast('Video exported', `${res.exported} frames of ${video.name} exported${where}.`, video.id, false, {
          label: 'View dataset',
          tab: 'dataset',
          videoId: video.id,
        });
      }
    } catch (err) {
      this.jobEvents.showToast('Export failed', err instanceof ApiError ? err.message : String(err), video.id, true);
    } finally {
      this.isExporting.set(false);
    }
  }
}
