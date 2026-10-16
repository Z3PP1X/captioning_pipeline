import { ChangeDetectionStrategy, Component, effect, inject, signal, viewChild } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideClock, lucideMaximize2, lucideZap, lucideFilm } from '@ng-icons/lucide';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { VideoPlayerComponent } from './video-player.component';
import { TimelineComponent } from './timeline.component';
import { ChunksPanelComponent } from './chunks-panel.component';
import { formatShortTime } from '../../core/utils/time.utils';
import { VideoStatusMenuComponent } from './video-status-menu.component';

@Component({
  selector: 'app-studio-view',
  imports: [
    NgIcon,
    VideoPlayerComponent,
    TimelineComponent,
    ChunksPanelComponent,
    VideoStatusMenuComponent,
  ],
  providers: [
    provideIcons({
      lucideClock,
      lucideMaximize2,
      lucideZap,
      lucideFilm,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full',
  },
  template: `
    <div class="flex flex-col gap-4 p-4 md:p-6 overflow-y-auto h-full flex-1 min-h-0">
      @let video = videoService.currentVideo();

      <!-- Video Header Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 shadow-xs">
        <div class="flex flex-col gap-1 min-w-0">
          <h1 class="font-display font-black text-xs uppercase tracking-wide text-muted-foreground">Chunking studio</h1>
          <!-- The filename is data: sentence case, never uppercased (studio-adaptations) -->
          <h2 class="font-sans font-bold text-base md:text-lg text-foreground break-all">
            {{ video ? video.name : 'Select a video from the queue' }}
          </h2>

          @if (video) {
            <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5">
                <ng-icon name="lucideClock" class="size-3 text-muted-foreground" />
                {{ formatShortTime(video.duration) }}
              </span>
              <span class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5">
                <ng-icon name="lucideMaximize2" class="size-3 text-muted-foreground" />
                {{ video.width }}x{{ video.height }}
              </span>
              <span class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5">
                <ng-icon name="lucideZap" class="size-3 text-muted-foreground" />
                <span class="tabular-nums">{{ video.fps.toFixed(1) }}</span> fps
              </span>
              <span class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5">
                <ng-icon name="lucideFilm" class="size-3 text-muted-foreground" />
                <span class="tabular-nums">{{ video.frame_count }}</span> frames
              </span>
            </div>
          }
        </div>

        @if (video) {
          <app-video-status-menu [video]="video" />
        }
      </div>

      <!-- Video Player & Stage -->
      <app-video-player
        #player
        (timeChange)="onPlayerTimeChange($event)"
      />

      <!-- Filmstrip Timeline -->
      <app-timeline
        [currentTime]="currentTime()"
        (seek)="onTimelineSeek($event)"
      />

      <!-- Chunks Management Panel -->
      <app-chunks-panel
        [currentTime]="currentTime()"
        (seek)="onTimelineSeek($event)"
      />
    </div>
  `,
})
export class StudioViewComponent {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  readonly player = viewChild<VideoPlayerComponent>('player');

  readonly currentTime = signal(0);
  readonly formatShortTime = formatShortTime;

  constructor() {
    // "Seek" from Curate switches to this view: wait until the player exists before consuming the request
    effect(() => {
      const seekTime = this.chunkService.requestedSeekTime();
      const player = this.player();
      if (seekTime === null || seekTime === undefined) return;
      this.currentTime.set(seekTime);
      if (!player) return;
      player.seek(seekTime);
      this.chunkService.requestedSeekTime.set(null);
    });
  }

  onPlayerTimeChange(time: number): void {
    this.currentTime.set(time);
  }

  onTimelineSeek(time: number): void {
    this.player()?.seek(time);
  }
}
