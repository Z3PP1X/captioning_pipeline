import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideAlertTriangle,
  lucideBan,
  lucideCaptions,
  lucideCheck,
  lucideCircleDashed,
  lucideEllipsis,
  lucideListChecks,
  lucideLoader2,
  lucideRotateCcw,
  lucideScissors,
  lucideSkipForward,
} from '@ng-icons/lucide';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmDropdownMenuImports } from '@spartan-ng/helm/dropdown-menu';
import { Video } from '../../core/models/video.model';
import { ChunkService } from '../../core/services/chunk.service';
import { ConfirmService } from '../../core/services/confirm.service';
import { JobEventService } from '../../core/services/job-event.service';
import { VideoService } from '../../core/services/video.service';
import {
  ALL_VIDEO_STATUSES,
  derivedVideoStatus,
  videoStatusLabel,
  videoStatusMeta,
} from '../../core/ui/status';

/**
 * Decision 3 (HANDOFF §3): the video status is shown, not edited. A badge renders the status through the shared
 * `videoStatusMeta` mapping (icon + text, live job progress); an overflow menu offers Skip, Restore (back to the
 * status the data implies, `derivedVideoStatus`) and an "Advanced: set status…" submenu with every status.
 * Jumps to a status other than the derived one ask for confirmation. All changes use `PATCH /api/videos/{id}`.
 */
@Component({
  selector: 'app-video-status-menu',
  imports: [NgIcon, HlmBadgeImports, HlmButtonImports, HlmDropdownMenuImports],
  providers: [
    provideIcons({
      lucideAlertTriangle,
      lucideBan,
      lucideCaptions,
      lucideCheck,
      lucideCircleDashed,
      lucideEllipsis,
      lucideListChecks,
      lucideLoader2,
      lucideRotateCcw,
      lucideScissors,
      lucideSkipForward,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'flex items-center gap-1.5' },
  template: `
    @let m = meta();
    <span hlmBadge [variant]="m.variant" class="gap-1 text-xs font-medium" data-testid="video-status-badge">
      <ng-icon [name]="m.icon" class="size-3" [class.motion-safe:animate-spin]="m.spin" aria-hidden="true" />
      <span class="sr-only">Status:</span>
      {{ m.label }}
    </span>

    <button
      hlmBtn
      variant="ghost"
      size="icon-sm"
      class="cursor-pointer"
      align="end"
      [hlmDropdownMenuTrigger]="statusMenu"
      [disabled]="busy()"
      aria-label="Status actions"
      data-testid="video-status-menu-trigger"
    >
      <ng-icon name="lucideEllipsis" class="size-4" aria-hidden="true" />
    </button>

    <ng-template #statusMenu>
      <hlm-dropdown-menu class="w-64">
        <hlm-dropdown-menu-label>Status</hlm-dropdown-menu-label>
        @if (!isSkipped()) {
          <button hlmDropdownMenuItem (triggered)="skip()">
            <ng-icon name="lucideSkipForward" aria-hidden="true" />
            Skip video
          </button>
        }
        @if (canRestore()) {
          <button hlmDropdownMenuItem (triggered)="restore()">
            <ng-icon name="lucideRotateCcw" aria-hidden="true" />
            Restore to {{ derivedLabel() }}
          </button>
        }
        <hlm-dropdown-menu-separator />
        <button hlmDropdownMenuItem [hlmDropdownMenuSubTrigger]="advancedMenu">
          Advanced: set status…
          <hlm-dropdown-menu-item-sub-indicator />
        </button>
      </hlm-dropdown-menu>
    </ng-template>

    <ng-template #advancedMenu>
      <hlm-dropdown-menu-sub class="w-60">
        @for (s of statuses; track s.value) {
          <button
            hlmDropdownMenuItem
            [disabled]="s.value === video().status"
            (triggered)="setStatus(s.value)"
            [attr.aria-current]="s.value === video().status ? 'true' : null"
          >
            @if (s.value === video().status) {
              <ng-icon name="lucideCheck" aria-hidden="true" />
            } @else {
              <span class="size-4 shrink-0" aria-hidden="true"></span>
            }
            {{ s.label }}
            @if (s.value === derived()) {
              <span class="ms-auto text-xs text-muted-foreground">Suggested</span>
            }
          </button>
        }
      </hlm-dropdown-menu-sub>
    </ng-template>
  `,
})
export class VideoStatusMenuComponent {
  private readonly videoService = inject(VideoService);
  private readonly chunkService = inject(ChunkService);
  private readonly jobEvents = inject(JobEventService);
  private readonly confirm = inject(ConfirmService);

  readonly video = input.required<Video>();

  readonly statuses = ALL_VIDEO_STATUSES;
  readonly busy = signal(false);

  readonly activeJob = computed(() => {
    const id = String(this.video().id);
    for (const job of this.jobEvents.activeJobs().values()) {
      if (String(job.video_id) === id) return job;
    }
    return null;
  });

  readonly meta = computed(() => videoStatusMeta(this.video(), this.activeJob()));

  /** Saved chunks of the open video count as well (the list's `chunk_count` is only refreshed on reload). */
  readonly derived = computed(() => {
    const v = this.video();
    const saved = this.chunkService.chunks().filter((c) => c.id !== null && c.id !== undefined).length;
    return derivedVideoStatus(v, Math.max(v.chunk_count ?? 0, saved));
  });
  readonly derivedLabel = computed(() => videoStatusLabel(this.derived()));

  readonly isSkipped = computed(() => this.video().status === 'skipped' || this.video().status === 'ignored');
  /** No restore while a job owns the status (extracting / captioning are reported by the job). */
  readonly canRestore = computed(() => !this.activeJob() && this.video().status !== this.derived());

  skip(): Promise<void> {
    return this.apply('skipped');
  }

  restore(): Promise<void> {
    return this.apply(this.derived());
  }

  async setStatus(status: string): Promise<void> {
    if (status === this.video().status) return;
    if (status !== this.derived()) {
      const label = videoStatusLabel(status);
      const ok = await this.confirm.ask({
        title: `Set status to "${label}"?`,
        description:
          `The video's data suggests "${this.derivedLabel()}". Setting "${label}" by hand can unlock or hide ` +
          'workflow steps, and the next library scan may change it back.',
        confirmLabel: `Set to ${label}`,
        destructive: false,
      });
      if (!ok) return;
    }
    await this.apply(status);
  }

  private async apply(status: string): Promise<void> {
    const v = this.video();
    this.busy.set(true);
    try {
      await this.videoService.updateStatus(v.id, status);
    } catch (err) {
      this.jobEvents.showToast(
        'Status not changed',
        err instanceof Error ? err.message : String(err),
        v.id,
        true,
      );
    } finally {
      this.busy.set(false);
    }
  }
}
