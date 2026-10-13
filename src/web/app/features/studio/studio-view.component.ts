import { ChangeDetectionStrategy, Component, ViewChild, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { VideoPlayerComponent } from './video-player.component';
import { TimelineComponent } from './timeline.component';
import { ChunksPanelComponent } from './chunks-panel.component';
import { formatShortTime } from '../../core/utils/time.utils';

@Component({
  selector: 'app-studio-view',
  imports: [
    FormsModule,
    VideoPlayerComponent,
    TimelineComponent,
    ChunksPanelComponent,
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
        <div class="flex flex-col gap-1">
          <h1 class="font-display font-black text-lg md:text-xl uppercase tracking-wide text-foreground">
            {{ video ? video.name : 'Select a video from the queue' }}
          </h1>

          @if (video) {
            <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span class="rounded bg-muted px-2 py-0.5">⏱️ {{ formatShortTime(video.duration) }}</span>
              <span class="rounded bg-muted px-2 py-0.5">📐 {{ video.width }}x{{ video.height }}</span>
              <span class="rounded bg-muted px-2 py-0.5">⚡ {{ video.fps.toFixed(1) }} fps</span>
              <span class="rounded bg-muted px-2 py-0.5">🎞️ {{ video.frame_count }} frames</span>
            </div>
          }
        </div>

        @if (video) {
          <div class="flex items-center gap-2 text-xs">
            <label class="font-medium text-muted-foreground">Status:</label>
            <select
              [ngModel]="video.status"
              (ngModelChange)="onStatusChange(video.id, $event)"
              class="h-8 rounded-md border border-input bg-background px-2.5 text-xs font-semibold text-foreground focus:ring-1 focus:ring-ring"
            >
              <option value="new">New</option>
              <option value="chunked">Chunked</option>
              <option value="extracting">Extracting</option>
              <option value="curating">Curating</option>
              <option value="exported">Exported</option>
              <option value="skipped">Skipped</option>
            </select>
          </div>
        }
      </div>

      <!-- Video Player & Stage -->
      <app-video-player
        #player
        (timeChange)="onPlayerTimeChange($event)"
      />

      <!-- Filmstrip Timeline -->
      <app-timeline
        [currentTime]="currentTime"
        (seek)="onTimelineSeek($event)"
      />

      <!-- Chunks Management Panel -->
      <app-chunks-panel
        [currentTime]="currentTime"
        (seek)="onTimelineSeek($event)"
      />
    </div>
  `,
})
export class StudioViewComponent {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);

  @ViewChild('player') playerRef!: VideoPlayerComponent;

  currentTime = 0;
  readonly formatShortTime = formatShortTime;

  constructor() {
    effect(() => {
      const seekTime = this.chunkService.requestedSeekTime();
      if (seekTime !== null && seekTime !== undefined) {
        this.currentTime = seekTime;
        if (this.playerRef) {
          this.playerRef.seek(seekTime);
        }
        this.chunkService.requestedSeekTime.set(null);
      }
    });
  }

  onPlayerTimeChange(time: number): void {
    this.currentTime = time;
  }

  onTimelineSeek(time: number): void {
    if (this.playerRef) {
      this.playerRef.seek(time);
    }
  }

  onStatusChange(videoId: string | number, status: string): void {
    this.videoService.updateStatus(videoId, status);
  }
}
