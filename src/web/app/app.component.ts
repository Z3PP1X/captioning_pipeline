import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  OnInit,
  effect,
  inject,
  signal,
} from '@angular/core';
import { HeaderComponent } from './features/shell/header.component';
import { VideoQueueComponent } from './features/shell/video-queue.component';
import { StudioViewComponent } from './features/studio/studio-view.component';
import { CurateViewComponent } from './features/curate/curate-view.component';
import { DatasetViewComponent } from './features/dataset/dataset-view.component';
import { LightboxModalComponent } from './features/modals/lightbox-modal.component';
import { SettingsModalComponent } from './features/modals/settings-modal.component';
import { OpenRouterModalComponent } from './features/modals/openrouter-modal.component';
import { ToastContainerComponent } from './features/shell/toast-container.component';
import { PromptClickerViewComponent } from './features/prompt-clicker/prompt-clicker-view.component';
import { PipelineBoardComponent, BoardNavigateEvent } from './features/pipeline-board/pipeline-board.component';
import { WorkflowBarComponent } from './features/shell/workflow-bar.component';
import { VideoService } from './core/services/video.service';
import { ChunkService } from './core/services/chunk.service';
import { CurateService } from './core/services/curate.service';
import { JobEventService, ToastAction } from './core/services/job-event.service';
import { SettingsService } from './core/services/settings.service';
import { DatasetService } from './core/services/dataset.service';
import { HotkeyService } from './core/services/hotkey.service';

@Component({
  selector: 'app-root',
  imports: [
    HeaderComponent,
    VideoQueueComponent,
    PipelineBoardComponent,
    WorkflowBarComponent,
    StudioViewComponent,
    CurateViewComponent,
    DatasetViewComponent,
    PromptClickerViewComponent,
    LightboxModalComponent,
    SettingsModalComponent,
    OpenRouterModalComponent,
    ToastContainerComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col h-screen w-screen overflow-hidden bg-canvas text-foreground">
      <a
        href="#main-content"
        class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-1.5 focus:rounded-md focus:bg-primary focus:text-primary-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring text-xs font-semibold"
      >
        Skip to main content
      </a>

      <!-- Top Header Navigation Bar -->
      <app-header
        [currentTab]="currentTab()"
        (tabChange)="onTabChange($event)"
        (nextVideoClick)="onNextVideo()"
        (openSettingsClick)="isSettingsModalOpen.set(true)"
      />

      <!-- Main Workspace Container -->
      <div class="flex flex-1 overflow-hidden">
        <!-- Left Sidebar: Video Queue -->
        @if (currentTab() !== 'prompt-clicker' && currentTab() !== 'board') {
          <app-video-queue />
        }

        <!-- Center Stage: Active Tab View -->
        <main id="main-content" tabindex="-1" class="flex-1 min-h-0 overflow-hidden relative flex flex-col outline-none">
          @if (currentTab() === 'studio' || currentTab() === 'curate') {
            <app-workflow-bar
              (tabChange)="onTabChange($event)"
              (openCaptionModal)="isOpenRouterModalOpen.set(true)"
            />
          }

          @switch (currentTab()) {
            @case ('board') {
              <app-pipeline-board
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (navigate)="onBoardNavigate($event)"
              />
            }
            @case ('studio') {
              <app-studio-view class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full" />
            }
            @case ('curate') {
              <app-curate-view
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (openVlmModal)="isOpenRouterModalOpen.set(true)"
                (seekChunk)="onSeekChunk($event)"
              />
            }
            @case ('dataset') {
              <app-dataset-view class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full" />
            }
            @case ('prompt-clicker') {
              <app-prompt-clicker-view class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full" />
            }
          }
        </main>
      </div>

      <!-- Modals & Overlays -->
      <app-lightbox-modal />

      <app-settings-modal
        [isOpen]="isSettingsModalOpen()"
        (close)="isSettingsModalOpen.set(false)"
      />

      <app-openrouter-modal
        [isOpen]="isOpenRouterModalOpen()"
        (close)="isOpenRouterModalOpen.set(false)"
      />

      <app-toast-container (actionRequest)="onToastAction($event)" />
    </div>
  `,
})
export class AppComponent implements OnInit {
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);
  readonly curateService = inject(CurateService);
  readonly jobEvents = inject(JobEventService);
  readonly settingsService = inject(SettingsService);
  readonly datasetService = inject(DatasetService);
  private readonly hotkeys = inject(HotkeyService);

  readonly currentTab = signal<'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker'>('board');
  readonly isSettingsModalOpen = signal<boolean>(false);
  readonly isOpenRouterModalOpen = signal<boolean>(false);

  constructor() {
    // React to video selection changes
    effect(() => {
      const video = this.videoService.currentVideo();
      if (video) {
        this.chunkService.loadChunks(video.id);
        this.chunkService.loadFilmstripAndCuts(video.id);
        if (this.currentTab() === 'curate') {
          this.curateService.loadCandidates(video.id);
        }
      }
    });
  }

  async ngOnInit(): Promise<void> {
    this.registerHotkeys();

    // Start SSE stream listener
    this.jobEvents.initSSE((videoId) => {
      this.videoService.loadVideos();
      const current = this.videoService.currentVideo();
      if (current && current.id === videoId) {
        this.curateService.loadCandidates(videoId);
      }
    });

    // Load initial settings and videos
    await this.settingsService.loadSettings();
    await this.videoService.loadVideos();
    await this.videoService.selectNextVideo();
  }

  @HostListener('window:keydown', ['$event'])
  onKeyDown(e: KeyboardEvent): void {
    this.hotkeys.handleKeyDown(e);
  }

  private registerHotkeys(): void {
    this.hotkeys.register('next_video', () => {
      this.onNextVideo();
    });

    this.hotkeys.register('escape', () => {
      this.curateService.closeLightbox();
      this.isSettingsModalOpen.set(false);
      this.isOpenRouterModalOpen.set(false);
    });

    this.hotkeys.register('keep', () => {
      const c = this.curateService.currentLightboxCandidate();
      if (c) {
        this.curateService.setCandidateState(c.video_id, c.id, 'keep');
        this.curateService.nextLightbox();
      }
    });

    this.hotkeys.register('reject', () => {
      const c = this.curateService.currentLightboxCandidate();
      if (c) {
        this.curateService.setCandidateState(c.video_id, c.id, 'reject');
        this.curateService.nextLightbox();
      }
    });

    this.hotkeys.register('star', () => {
      const c = this.curateService.currentLightboxCandidate();
      if (c) {
        this.curateService.setCandidateState(c.video_id, c.id, 'star');
        this.curateService.nextLightbox();
      }
    });

    this.hotkeys.register('lightbox_prev', () => {
      this.curateService.prevLightbox();
    });

    this.hotkeys.register('lightbox_next', () => {
      this.curateService.nextLightbox();
    });
  }

  onTabChange(tab: 'board' | 'studio' | 'curate' | 'dataset' | 'prompt-clicker'): void {
    this.currentTab.set(tab);
    const video = this.videoService.currentVideo();
    if (tab === 'curate' && video) {
      this.curateService.loadCandidates(video.id);
    } else if (tab === 'dataset') {
      this.datasetService.loadDataset();
    }
  }

  onBoardNavigate(ev: BoardNavigateEvent): void {
    this.videoService.selectVideo(ev.videoId);
    this.onTabChange(ev.tab);
  }

  onNextVideo(): void {
    this.videoService.selectNextVideo();
  }

  onCurateRequest(videoId: string): void {
    this.videoService.selectVideo(videoId);
    this.onTabChange('curate');
  }

  onToastAction(action: ToastAction): void {
    if (action.videoId) {
      this.videoService.selectVideo(String(action.videoId));
    }
    this.onTabChange(action.tab);
  }

  onSeekChunk(time: number): void {
    this.chunkService.requestedSeekTime.set(time);
    this.onTabChange('studio');
  }
}
