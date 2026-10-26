import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  OnInit,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HeaderComponent } from './features/shell/header.component';
import { VideoQueueComponent } from './features/shell/video-queue.component';
import { StudioViewComponent } from './features/studio/studio-view.component';
import { CurateViewComponent } from './features/curate/curate-view.component';
import { DatasetViewComponent } from './features/dataset/dataset-view.component';
import { LightboxModalComponent } from './features/modals/lightbox-modal.component';
import { SettingsModalComponent } from './features/modals/settings-modal.component';
import { OpenRouterModalComponent } from './features/modals/openrouter-modal.component';
import { PersonModalComponent } from './features/modals/person-modal.component';
import { TagManagerModalComponent } from './features/modals/tag-manager-modal.component';
import { ToastContainerComponent } from './features/shell/toast-container.component';
import { ConfirmDialogComponent } from './features/shell/confirm-dialog.component';
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
import { TagService } from './core/services/tag.service';
import { UiStateService } from './core/services/ui-state.service';
import { AppTab, buildHashRoute, isRouteHash, parseHashRoute } from './core/utils/hash-route.utils';
import { buildStudioHash, parseStudioHash } from './core/utils/studio-route.utils';

// New 5-Step Guided Studio Components & Services
import { StudioApiService } from './services/studio-api.service';
import { StudioStep } from './models/studio.models';
import { Step1IngestComponent } from './pages/step1-ingest/step1-ingest.component';
import { Step2TriageComponent } from './pages/step2-triage/step2-triage.component';
import { Step3TaggingComponent } from './pages/step3-tagging/step3-tagging.component';
import { Step4CaptionComponent } from './pages/step4-caption/step4-caption.component';
import { Step5ExportRagComponent } from './pages/step5-export-rag/step5-export-rag.component';
import { SettingsSkillsModalComponent } from './pages/settings-skills/settings-skills-modal.component';

@Component({
  selector: 'app-root',
  imports: [
    FormsModule,
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
    PersonModalComponent,
    TagManagerModalComponent,
    ToastContainerComponent,
    ConfirmDialogComponent,
    Step1IngestComponent,
    Step2TriageComponent,
    Step3TaggingComponent,
    Step4CaptionComponent,
    Step5ExportRagComponent,
    SettingsSkillsModalComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <a
        href="#main-content"
        (click)="skipToMain($event)"
        class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:min-h-8 focus:min-w-8 focus:px-3 focus:py-1.5 focus:rounded-md focus:bg-orange-700 text-white text-xs font-semibold"
      >
        Skip to main content
      </a>

      @if (studioMode() === 'stepper') {
        <!-- ================================================================
             NEW 5-STEP GUIDED WORKFLOW STEPPER & NAVIGATION BAR
             ================================================================ -->
        <header class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 border-b border-slate-800 bg-slate-900 select-none z-40 shrink-0">
          <!-- Brand + Active Dataset Selector -->
          <div class="flex items-center gap-3">
            <div class="flex items-center gap-2">
              <span class="flex items-center justify-center w-8 h-8 rounded-lg bg-orange-700 text-white font-black text-sm shadow">
                L2
              </span>
              <div class="flex flex-col">
                <span class="font-bold text-sm tracking-wide text-white leading-tight">
                  LoRA Dataset & Caption Studio
                </span>
                <span class="text-xs font-mono text-slate-300">
                  Zero-ML CV • 3-Tier OpenRouter Cascade • FTS5 RAG
                </span>
              </div>
            </div>

            <!-- Dataset Dropdown Selector -->
            <div class="flex items-center gap-1.5 bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 min-h-8">
              <label for="studio-dataset-select" class="text-xs font-semibold text-slate-300">Dataset:</label>
              <select
                id="studio-dataset-select"
                [ngModel]="studioApi.activeDatasetId()"
                (ngModelChange)="onSelectStudioDataset($event)"
                aria-label="Active Studio Dataset"
                class="w-48 truncate bg-slate-950 text-xs font-mono text-orange-300 focus:outline-none cursor-pointer"
              >
                @for (ds of studioApi.datasets(); track ds.id) {
                  <option [value]="ds.id" class="bg-slate-900 text-white">
                    {{ ds.name }} ({{ ds.kept_items }}/{{ ds.total_items }} kept)
                  </option>
                }
                @if (studioApi.datasets().length === 0) {
                  <option [ngValue]="null" class="bg-slate-900 text-slate-300">No dataset yet</option>
                }
              </select>
            </div>
          </div>

          <!-- Center: 5-Step Guided Workflow Stepper -->
          <nav class="flex flex-wrap items-center gap-1 bg-slate-950 p-1 rounded-xl border border-slate-800" aria-label="5-Step Guided Workflow">
            @for (st of workflowSteps; track st.id; let idx = $index) {
              <button
                type="button"
                (click)="studioApi.activeStep.set(st.id)"
                [title]="st.label"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 cursor-pointer"
                [class.bg-orange-700]="studioApi.activeStep() === st.id"
                [class.text-white]="studioApi.activeStep() === st.id"
                [class.shadow]="studioApi.activeStep() === st.id"
                [class.text-slate-300]="studioApi.activeStep() !== st.id"
                [class.hover:text-white]="studioApi.activeStep() !== st.id"
              >
                <span
                  class="w-5 h-5 rounded-full text-xs font-mono font-bold flex items-center justify-center text-white"
                  [class.bg-orange-900]="studioApi.activeStep() === st.id"
                  [class.bg-slate-800]="studioApi.activeStep() !== st.id"
                >
                  {{ idx + 1 }}
                </span>
                <span>{{ st.label }}</span>
                @if (st.id === 'captioning') {
                  <span
                    class="min-w-5 px-1.5 py-0.5 rounded-full bg-rose-800 text-white text-xs font-mono text-center"
                    title="Items needing manual caption review"
                  >
                    {{ studioApi.activeDataset()?.needs_manual_items ?? 0 }}
                  </span>
                }
              </button>
            }
          </nav>

          <!-- Right: Live SSE Job Telemetry, Shortcut Help (?), Settings & Skills, Legacy Switch -->
          <div class="flex items-center gap-2">
            <!-- Live Job Progress Pill -->
            @if (studioApi.activeJob(); as job) {
              <div class="flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-950 border border-indigo-500/40 text-xs font-mono">
                <span class="w-2 h-2 rounded-full bg-indigo-400 animate-ping"></span>
                <span class="text-indigo-200 truncate max-w-[180px]" [title]="job.step">{{ job.step }}</span>
                <span class="text-white font-bold">{{ (job.progress * 100).toFixed(0) }}%</span>
              </div>
            }

            <!-- SSE Connection Pill -->
            <div
              class="flex items-center gap-1.5 px-2.5 py-1.5 min-h-8 rounded-full bg-slate-950 border border-slate-800 text-xs font-mono text-slate-200"
              [title]="'Real-time SSE telemetry: ' + studioApi.sseState()"
            >
              <span
                class="w-2 h-2 rounded-full"
                [class.bg-emerald-400]="studioApi.sseState() === 'connected'"
                [class.bg-amber-400]="studioApi.sseState() === 'connecting'"
                [class.bg-rose-500]="studioApi.sseState() === 'disconnected'"
              ></span>
              <span>SSE</span>
            </div>

            <!-- Keyboard Shortcut Cheat-Sheet Button (?) -->
            <button
              type="button"
              (click)="isShortcutHelpOpen.set(!isShortcutHelpOpen())"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-100 border border-slate-700 cursor-pointer"
              title="Keyboard Shortcuts Cheat-Sheet (?)"
            >
              ? Keys
            </button>

            <!-- System Settings & Master Skills Manager Button -->
            <button
              type="button"
              data-testid="open-studio-settings"
              (click)="isStudioSettingsOpen.set(true)"
              title="Open System Settings & Krea 2 Master Skill Manager"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-orange-300 border border-orange-500/40 cursor-pointer"
            >
              ⚙️ Settings & Skills
            </button>

            <!-- Toggle Classic Legacy View -->
            <button
              type="button"
              (click)="studioMode.set('legacy')"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 text-xs text-slate-200 border border-slate-800 cursor-pointer"
              title="Switch to Classic Video Queue Workspace"
            >
              Classic UI
            </button>
          </div>
        </header>

        <!-- Toast Notification Banner -->
        @if (studioApi.toastMessage(); as toast) {
          <div
            class="fixed bottom-10 right-5 z-50 px-4 py-2.5 rounded-xl border shadow-2xl text-xs font-semibold flex items-center gap-2"
            [class.bg-emerald-950]="toast.tone === 'success'"
            [class.border-emerald-500]="toast.tone === 'success'"
            [class.text-emerald-200]="toast.tone === 'success'"
            [class.bg-amber-950]="toast.tone === 'warning'"
            [class.border-amber-500]="toast.tone === 'warning'"
            [class.text-amber-200]="toast.tone === 'warning'"
            [class.bg-rose-950]="toast.tone === 'error'"
            [class.border-rose-500]="toast.tone === 'error'"
            [class.text-rose-200]="toast.tone === 'error'"
            [class.bg-slate-900]="toast.tone === 'info'"
            [class.border-slate-700]="toast.tone === 'info'"
            [class.text-slate-200]="toast.tone === 'info'"
          >
            <span>{{ toast.text }}</span>
          </div>
        }

        <!-- Active Step View Stage -->
        <main id="main-content" tabindex="-1" class="flex-1 min-h-0 overflow-hidden relative flex flex-col outline-none">
          @switch (studioApi.activeStep()) {
            @case ('ingest') {
              <app-step1-ingest
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (stepNavigate)="studioApi.activeStep.set($event)"
              />
            }
            @case ('triage') {
              <app-step2-triage
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (stepNavigate)="studioApi.activeStep.set($event)"
              />
            }
            @case ('tagging') {
              <app-step3-tagging
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (stepNavigate)="studioApi.activeStep.set($event)"
              />
            }
            @case ('captioning') {
              <app-step4-caption
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
                (stepNavigate)="studioApi.activeStep.set($event)"
              />
            }
            @case ('export_rag') {
              <app-step5-export-rag
                class="flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full"
              />
            }
          }
        </main>

        <!-- Bottom Studio Status & Telemetry Footer -->
        <footer class="h-8 px-4 border-t border-slate-800 bg-slate-900 flex items-center justify-between text-xs font-mono text-slate-300 shrink-0 select-none overflow-hidden">
          <div class="flex items-center gap-4">
            <span>
              SQLite WAL:
              <strong [class.text-emerald-300]="studioApi.health()?.sqlite_wal" [class.text-amber-300]="!studioApi.health()?.sqlite_wal">
                {{ studioApi.health()?.sqlite_wal ? 'ACTIVE' : 'INIT' }}
              </strong>
            </span>
            <span>
              FFmpeg:
              <strong [class.text-emerald-300]="studioApi.health()?.ffmpeg" [class.text-rose-300]="!studioApi.health()?.ffmpeg">
                {{ studioApi.health()?.ffmpeg ? 'READY' : 'MISSING' }}
              </strong>
            </span>
            <span>
              FTS5 BM25:
              <strong class="text-emerald-300">
                {{ studioApi.health()?.fts5 ? 'INDEXED' : 'INIT' }}
              </strong>
            </span>
          </div>

          <div class="flex items-center gap-4">
            <span>Dataset: <strong class="text-slate-100">{{ studioApi.activeDataset()?.name ?? '—' }}</strong></span>
            <span>Trigger: <strong class="text-sky-300">{{ studioApi.activeDataset()?.trigger_word ?? 'ohwx_subject' }}</strong> ({{ studioApi.activeDataset()?.trigger_position ?? 'prefix' }})</span>
            <span>Kept: <strong class="text-emerald-300">{{ studioApi.activeDataset()?.kept_items ?? 0 }}</strong> / {{ studioApi.activeDataset()?.total_items ?? 0 }}</span>
            <span>Captioned: <strong class="text-indigo-300">{{ studioApi.activeDataset()?.captioned_items ?? 0 }}</strong></span>
          </div>
        </footer>

        <!-- System Settings & Versionable Master Skill Manager Modal -->
        <app-settings-skills-modal
          [isOpen]="isStudioSettingsOpen()"
          (closeModal)="isStudioSettingsOpen.set(false)"
        />

        <!-- Keyboard Shortcut Cheat-Sheet Overlay (?) -->
        @if (isShortcutHelpOpen()) {
          <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-xs p-4">
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="shortcut-help-title"
              class="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-6 text-slate-100 shadow-2xl flex flex-col gap-4"
            >
              <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 id="shortcut-help-title" class="text-sm font-bold uppercase tracking-wider text-orange-300">
                  HITL Studio Keyboard Shortcuts Cheat-Sheet
                </h3>
                <button
                  type="button"
                  (click)="isShortcutHelpOpen.set(false)"
                  class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 text-xs text-slate-200 cursor-pointer"
                >
                  Esc
                </button>
              </div>
              <div class="grid grid-cols-2 gap-2.5 text-xs font-mono">
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Navigate Grid</span>
                  <kbd class="text-white font-bold">← ↑ ↓ →</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Keep Item</span>
                  <kbd class="text-emerald-300 font-bold">Space / Enter</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Reject Item</span>
                  <kbd class="text-rose-300 font-bold">X / Backspace</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Star Item</span>
                  <kbd class="text-amber-300 font-bold">S</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Quick-Tag 1..9</span>
                  <kbd class="text-sky-300 font-bold">1 – 9</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Cluster Compare</span>
                  <kbd class="text-indigo-300 font-bold">C</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Force Re-Caption</span>
                  <kbd class="text-orange-300 font-bold">R</kbd>
                </div>
                <div class="p-2 rounded bg-slate-950 border border-slate-800 flex justify-between">
                  <span class="text-slate-300">Shortcut Help</span>
                  <kbd class="text-slate-200 font-bold">?</kbd>
                </div>
              </div>
            </div>
          </div>
        }
      } @else {
        <!-- ================================================================
             CLASSIC LEGACY WORKSPACE (Preserved for 100% backward compatibility)
             ================================================================ -->
        <div class="flex items-center justify-between bg-slate-900 px-4 py-1.5 border-b border-slate-800 text-xs">
          <span class="text-slate-300">Classic Legacy Workspace Mode</span>
          <button
            type="button"
            (click)="studioMode.set('stepper')"
            class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-orange-700 text-white font-semibold cursor-pointer"
          >
            Return to 5-Step Guided Studio →
          </button>
        </div>

        <app-header
          [currentTab]="currentTab()"
          (tabChange)="onTabChange($event)"
          (nextVideoClick)="onNextVideo()"
          (openSettingsClick)="isSettingsModalOpen.set(true)"
        />

        <div class="flex flex-1 overflow-hidden">
          @if (currentTab() !== 'prompt-clicker' && currentTab() !== 'board') {
            <app-video-queue />
          }

          <main id="main-content" tabindex="-1" class="flex-1 min-h-0 overflow-hidden relative flex flex-col outline-none">
            @if (currentTab() === 'studio' || currentTab() === 'curate') {
              <app-workflow-bar
                (tabChange)="onTabChange($event)"
                (openCaptionModal)="isOpenRouterModalOpen.set(true)"
                (openPersonModal)="openPersonModal()"
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
                  (openPersonModal)="openPersonModal($event.frameId)"
                  (openTagManager)="isTagManagerOpen.set(true)"
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

        <app-lightbox-modal (tagPerson)="openPersonModal($event)" />
        <app-settings-modal [isOpen]="isSettingsModalOpen()" (close)="isSettingsModalOpen.set(false)" />
        <app-openrouter-modal [isOpen]="isOpenRouterModalOpen()" (close)="isOpenRouterModalOpen.set(false)" />

        @if (isPersonModalOpen()) {
          <app-person-modal [frameId]="personModalFrameId()" (close)="isPersonModalOpen.set(false)" />
        }
        @if (isTagManagerOpen()) {
          <app-tag-manager-modal (close)="isTagManagerOpen.set(false)" />
        }

        <app-toast-container (actionRequest)="onToastAction($event)" />
        <app-confirm-dialog />
      }
    </div>
  `,
})
export class AppComponent implements OnInit {
  // New Studio v2 Service & Signals
  readonly studioApi = inject(StudioApiService);
  readonly studioMode = signal<'stepper' | 'legacy'>('stepper');
  readonly isStudioSettingsOpen = signal<boolean>(false);
  readonly isShortcutHelpOpen = signal<boolean>(false);

  readonly workflowSteps: Array<{ id: StudioStep; label: string }> = [
    { id: 'ingest', label: '1. Ingest (Images/Video)' },
    { id: 'triage', label: '2. Auto-Filter & HITL Triage' },
    { id: 'tagging', label: '3. Tagging & Stratification' },
    { id: 'captioning', label: '4. OpenRouter Caption & Review' },
    { id: 'export_rag', label: '5. Export & LoRA Prompt Studio' },
  ];

  // Legacy services & signals preserved for backward compatibility
  readonly videoService = inject(VideoService);
  readonly chunkService = inject(ChunkService);
  readonly curateService = inject(CurateService);
  readonly jobEvents = inject(JobEventService);
  readonly settingsService = inject(SettingsService);
  readonly datasetService = inject(DatasetService);
  private readonly hotkeys = inject(HotkeyService);
  private readonly tagService = inject(TagService);
  private readonly uiState = inject(UiStateService);
  private readonly document = inject(DOCUMENT);

  readonly currentTab = signal<AppTab>('board');
  private readonly routeReady = signal(false);
  private initialHashPending = true;
  readonly isSettingsModalOpen = signal<boolean>(false);
  readonly isOpenRouterModalOpen = signal<boolean>(false);
  readonly isPersonModalOpen = signal<boolean>(false);
  readonly isTagManagerOpen = signal<boolean>(false);
  readonly personModalFrameId = signal<number | null>(null);

  openPersonModal(frameId?: number): void {
    this.personModalFrameId.set(frameId ?? null);
    this.isPersonModalOpen.set(true);
  }

  constructor() {
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

    effect(() => {
      const ev = this.jobEvents.tagsUpdated();
      const video = untracked(() => this.videoService.currentVideo());
      if (!ev || !video || untracked(() => this.currentTab()) !== 'curate') return;
      if (ev.videoId === null || ev.videoId === String(video.id)) {
        this.curateService.refreshTags(video.id).catch(() => undefined);
        this.tagService.refreshCurrentFrame();
      }
    });

    // Studio deep link: mirror the active step into `#/studio/<step>` (no history entry per step)
    effect(() => {
      if (!this.routeReady() || this.studioMode() !== 'stepper') return;
      const hash = buildStudioHash(this.studioApi.activeStep());
      const win = this.document.defaultView;
      if (win && win.location.hash !== hash) win.history.replaceState(win.history.state, '', hash);
    });

    effect(() => {
      if (!this.routeReady() || this.studioMode() !== 'legacy') return;
      const hash = buildHashRoute(this.currentTab(), this.videoService.currentVideo()?.id ?? null);
      const first = this.initialHashPending;
      this.initialHashPending = false;
      const win = this.document.defaultView;
      if (!win || win.location.hash === hash) return;
      if (first) {
        win.history.replaceState(win.history.state, '', hash);
      } else {
        win.location.hash = hash;
      }
    });
  }

  async ngOnInit(): Promise<void> {
    this.registerHotkeys();
    this.initStudioTelemetry();
    const initial = this.applyStartRoute();
    await this.loadLegacyWorkspace(initial.videoId, initial.tab);
    this.routeReady.set(true);
  }

  private initStudioTelemetry(): void {
    this.studioApi.connectTelemetryStream();
    void this.studioApi.loadHealth();
    void this.studioApi.loadSettings();
    void this.studioApi.loadSkills();
    void this.studioApi.refreshDatasets();

    this.jobEvents.initSSE((videoId) => {
      this.videoService.loadVideos();
      const current = this.videoService.currentVideo();
      if (current && current.id === videoId) {
        this.curateService.loadCandidates(videoId);
      }
    });
  }

  private applyStartRoute(): { tab: AppTab; videoId: string | null } {
    const startHash = this.document.defaultView?.location.hash;
    const studioStep = parseStudioHash(startHash);
    if (studioStep) {
      this.studioMode.set('stepper');
      this.studioApi.activeStep.set(studioStep);
    } else if (startHash && isRouteHash(startHash) && startHash !== '#' && startHash !== '#/') {
      // A legacy deep link (`#/curate?video=...`) opens the classic workspace
      this.studioMode.set('legacy');
    }
    const initial = parseHashRoute(studioStep ? '' : startHash);
    this.currentTab.set(initial.tab);
    return initial;
  }

  private async loadLegacyWorkspace(videoId: string | null, tab: AppTab): Promise<void> {
    await this.settingsService.loadSettings();
    await this.videoService.loadVideos();
    const linked = videoId ? await this.videoService.selectVideo(videoId) : null;
    if (!linked) {
      await this.videoService.selectNextVideo();
    }
    if (tab === 'dataset') {
      this.datasetService.loadDataset();
    }
  }

  async onSelectStudioDataset(datasetId: string | null): Promise<void> {
    if (!datasetId) return;
    this.studioApi.activeDatasetId.set(datasetId);
    await this.studioApi.loadMediaItems(datasetId, { include_duplicates: true });
    await this.studioApi.loadStratification(datasetId);
    await this.studioApi.loadRejections(datasetId);
  }

  private isEditableTarget(target: HTMLElement | null): boolean {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
  }

  @HostListener('window:keydown', ['$event'])
  onKeyDown(e: KeyboardEvent): void {
    const isInput = this.isEditableTarget(e.target as HTMLElement | null);
    if (!isInput && e.key === '?') {
      e.preventDefault();
      this.isShortcutHelpOpen.update((v) => !v);
      return;
    }
    if (e.key === 'Escape' && (this.isShortcutHelpOpen() || this.isStudioSettingsOpen())) {
      this.isShortcutHelpOpen.set(false);
      this.isStudioSettingsOpen.set(false);
      return;
    }
    if (this.studioMode() === 'legacy') {
      this.hotkeys.handleKeyDown(e);
    }
  }

  @HostListener('window:hashchange')
  async onHashChange(): Promise<void> {
    const hash = this.document.defaultView?.location.hash ?? '';
    const studioStep = parseStudioHash(hash);
    if (studioStep) {
      this.studioMode.set('stepper');
      this.studioApi.activeStep.set(studioStep);
      return;
    }
    if (!this.routeReady() || !isRouteHash(hash)) return;
    const route = parseHashRoute(hash);
    this.routeReady.set(false);
    try {
      const current = this.videoService.currentVideo();
      if (route.videoId && String(current?.id) !== route.videoId) {
        await this.videoService.selectVideo(route.videoId);
      }
      if (route.tab !== this.currentTab()) {
        this.onTabChange(route.tab);
      }
    } finally {
      this.routeReady.set(true);
    }
  }

  skipToMain(e: Event): void {
    e.preventDefault();
    this.document.getElementById('main-content')?.focus();
  }

  private registerHotkeys(): void {
    this.hotkeys.register('next_video', () => {
      this.onNextVideo();
    });

    this.hotkeys.register('escape', () => {
      if (this.uiState.modalOpen()) {
        this.isSettingsModalOpen.set(false);
        this.isOpenRouterModalOpen.set(false);
        this.isPersonModalOpen.set(false);
        this.isTagManagerOpen.set(false);
        return;
      }
      if (this.tagService.drawMode() || this.tagService.pendingBox()) {
        this.tagService.drawMode.set(false);
        this.tagService.pendingBox.set(null);
        return;
      }
      this.curateService.closeLightbox();
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

    this.hotkeys.register('tag_region', () => {
      this.tagService.pendingBox.set(null);
      this.tagService.drawMode.update((on) => !on);
    });
  }

  onTabChange(tab: AppTab): void {
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
