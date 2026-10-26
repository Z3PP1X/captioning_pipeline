import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  ExtractionMode,
  FsBrowseResponse,
  SourceType,
  StudioDataset,
  StudioStep,
  TriggerPosition,
} from '../../models/studio.models';
import { StudioApiService } from '../../services/studio-api.service';

@Component({
  selector: 'app-step1-ingest',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div data-testid="view-ingest" class="flex flex-col lg:flex-row h-full w-full gap-4 p-4 overflow-y-auto bg-slate-950 text-slate-100">
      <!-- Left Column: /data/input Browser & Existing Datasets -->
      <div class="flex flex-col w-full lg:w-5/12 gap-4">
        <!-- Filesystem Browser Card -->
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-bold uppercase tracking-wider text-orange-300">
                1A. Input Media Browser (/data/input)
              </h2>
              <p class="text-xs text-slate-300 mt-0.5">
                Select a pre-sorted image folder or raw video container for non-ML extraction
              </p>
            </div>
            <button
              type="button"
              (click)="browse(currentSubpath())"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-md text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 cursor-pointer"
            >
              Refresh
            </button>
          </div>

          <!-- Current Path Bar -->
          <div class="flex items-center gap-2 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono">
            <span class="text-slate-300 shrink-0">Path:</span>
            <span
              class="text-emerald-300 truncate flex-1"
              [title]="fsState()?.current_path || '/data/input'"
            >{{ fsState()?.current_path || '/data/input' }}</span>
            @if (currentSubpath()) {
              <button
                type="button"
                (click)="navigateUp()"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 cursor-pointer"
              >
                .. Up
              </button>
            }
          </div>

          <!-- Current Folder Stats & Use Folder Action -->
          <div class="flex items-center justify-between bg-slate-950 border border-slate-800 rounded-lg px-3 py-2">
            <div class="flex items-center gap-3 text-xs">
              <span class="px-2 py-0.5 rounded bg-sky-950 text-sky-300 border border-sky-500/30 font-mono">
                {{ fsState()?.image_counts ?? 0 }} images
              </span>
              <span class="px-2 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-500/30 font-mono">
                {{ fsState()?.videos?.length ?? 0 }} videos
              </span>
            </div>
            <button
              type="button"
              (click)="selectCurrentFolderAsSource()"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-md text-xs font-semibold bg-sky-700 hover:bg-sky-600 text-white cursor-pointer transition"
            >
              Use Current Folder (Images)
            </button>
          </div>

          <!-- Directory & Video Listing -->
          <div class="max-h-56 overflow-y-auto divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-950">
            @for (dir of fsState()?.directories ?? []; track dir) {
              <div class="flex items-center justify-between gap-2 px-3 py-2 hover:bg-slate-900 text-xs">
                <button
                  type="button"
                  (click)="enterDirectory(dir)"
                  [title]="'Open folder ' + dir"
                  class="min-h-8 min-w-8 px-2 py-1 flex items-center gap-2 text-left font-medium text-amber-300 hover:underline cursor-pointer flex-1 truncate"
                >
                  <span>📁</span>
                  <span class="truncate" [title]="dir">{{ dir }}/</span>
                </button>
                <button
                  type="button"
                  (click)="selectSubfolderAsSource(dir)"
                  [title]="'Select folder ' + dir"
                  class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-sky-700 text-slate-200 hover:text-white text-xs cursor-pointer"
                >
                  Select Folder
                </button>
              </div>
            }

            @for (vid of fsState()?.videos ?? []; track vid) {
              <div class="flex items-center justify-between gap-2 px-3 py-2 hover:bg-slate-900 text-xs">
                <div class="flex items-center gap-2 text-purple-300 truncate flex-1" [title]="vid">
                  <span>🎬</span>
                  <span class="font-mono truncate" [title]="vid">{{ vid }}</span>
                </div>
                <button
                  type="button"
                  (click)="selectVideoAsSource(vid)"
                  [title]="'Select video ' + vid"
                  class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-purple-700 hover:bg-purple-600 text-white text-xs font-semibold cursor-pointer"
                >
                  Select Video
                </button>
              </div>
            }

            @if ((fsState()?.directories?.length ?? 0) === 0 && (fsState()?.videos?.length ?? 0) === 0) {
              <div class="p-4 text-center text-xs text-slate-300">
                No subdirectories or video files found in this path. You can type any path directly in the form.
              </div>
            }
          </div>
        </section>

        <!-- Existing Datasets Card -->
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg flex-1 flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-bold uppercase tracking-wider text-slate-200">
              Existing Studio Datasets ({{ api.datasets().length }})
            </h2>
            <button
              type="button"
              (click)="api.refreshDatasets()"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 cursor-pointer"
            >
              Reload
            </button>
          </div>

          <div class="flex-1 overflow-y-auto flex flex-col gap-2 max-h-80">
            @for (ds of api.datasets(); track ds.id) {
              <div
                class="p-3 rounded-lg border transition flex flex-col gap-2"
                [class.border-orange-500]="api.activeDataset()?.id === ds.id"
                [class.bg-slate-800]="api.activeDataset()?.id === ds.id"
                [class.border-slate-800]="api.activeDataset()?.id !== ds.id"
                [class.bg-slate-950]="api.activeDataset()?.id !== ds.id"
              >
                <div class="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    (click)="selectExistingDataset(ds)"
                    [title]="'Select dataset ' + ds.name"
                    class="min-h-8 min-w-8 px-2 py-1 rounded flex items-center gap-2 text-left cursor-pointer hover:bg-slate-900"
                  >
                    <span class="font-bold text-sm text-white">{{ ds.name }}</span>
                    <span class="px-1.5 py-0.5 text-xs font-mono uppercase rounded bg-slate-900 text-slate-300 border border-slate-700">
                      {{ ds.source_type }}
                    </span>
                  </button>
                  <span
                    class="px-2 py-0.5 text-xs font-mono rounded-full border"
                    [class.bg-emerald-950]="ds.status === 'ready_to_export' || ds.status === 'exported'"
                    [class.text-emerald-300]="ds.status === 'ready_to_export' || ds.status === 'exported'"
                    [class.border-emerald-500/40]="ds.status === 'ready_to_export' || ds.status === 'exported'"
                    [class.bg-amber-950]="ds.status === 'curating' || ds.status === 'captioning'"
                    [class.text-amber-300]="ds.status === 'curating' || ds.status === 'captioning'"
                    [class.border-amber-500/40]="ds.status === 'curating' || ds.status === 'captioning'"
                  >
                    {{ ds.status }}
                  </span>
                </div>

                <div class="flex flex-wrap items-center gap-3 text-xs text-slate-300 font-mono">
                  <span>Trigger: <strong class="text-sky-300">{{ ds.trigger_word }}</strong> ({{ ds.trigger_position }})</span>
                  <span>Total: <strong class="text-slate-100">{{ ds.total_items }}</strong></span>
                  <span>Kept: <strong class="text-emerald-300">{{ ds.kept_items }}</strong></span>
                  <span>Captioned: <strong class="text-indigo-300">{{ ds.captioned_items }}</strong></span>
                  @if (ds.needs_manual_items > 0) {
                    <span class="text-rose-300 font-bold">Manual Queue: {{ ds.needs_manual_items }}</span>
                  }
                </div>

                <div class="flex items-center justify-end gap-2 pt-1">
                  <button
                    type="button"
                    (click)="reclusterExisting(ds, $event)"
                    class="min-h-8 min-w-8 px-3 py-1.5 rounded text-xs bg-indigo-950 hover:bg-indigo-900 text-indigo-200 border border-indigo-500/30 cursor-pointer"
                  >
                    Re-Cluster (τ={{ dedupHammingThreshold() }})
                  </button>
                  <button
                    type="button"
                    (click)="openInTriage(ds, $event)"
                    class="min-h-8 min-w-8 px-3 py-1.5 rounded text-xs font-semibold bg-orange-700 hover:bg-orange-600 text-white cursor-pointer"
                  >
                    Open in Step 2 Triage →
                  </button>
                </div>
              </div>
            }

            @if (api.datasets().length === 0) {
              <div class="p-6 text-center text-xs text-slate-300 border border-dashed border-slate-800 rounded-lg">
                No datasets created yet. Configure & launch your first dataset ingestion on the right.
              </div>
            }
          </div>
        </section>
      </div>

      <!-- Right Column: Dual-Path Ingest & Mathematical CV Configuration -->
      <div class="flex flex-col w-full lg:w-7/12 gap-4">
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg flex flex-col gap-5">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-base font-bold text-white">
                1B. Dual-Path Dataset Ingestion & Mathematical Quality Pipeline
              </h2>
              <p class="text-xs text-slate-300">
                Zero local ML weights: Laplacian + Tenengrad sharpness, Chiaroscuro exposure, Shannon entropy, and O(N log N) BK-Tree pHash clustering
              </p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-xs font-mono bg-emerald-950 text-emerald-300 border border-emerald-500/30">
              &lt; 5ms / frame CV
            </span>
          </div>

          <!-- Dual Source Mode Switcher -->
          <div class="grid grid-cols-2 gap-3">
            <button
              type="button"
              (click)="sourceType.set('image_folder')"
              class="min-h-8 min-w-8 p-3 rounded-xl border text-left transition cursor-pointer flex flex-col gap-1"
              [class.border-sky-500]="sourceType() === 'image_folder'"
              [class.bg-sky-950]="sourceType() === 'image_folder'"
              [class.border-slate-800]="sourceType() !== 'image_folder'"
              [class.bg-slate-950]="sourceType() !== 'image_folder'"
            >
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold uppercase tracking-wider text-sky-300">Path 1: Pre-Sorted Images</span>
                <span class="text-xs font-mono text-slate-300">.png / .jpg / .webp</span>
              </div>
              <p class="text-xs text-slate-200">
                Recursively scan an image directory, import sidecar .txt captions, score quality Q, and collapse near-duplicates.
              </p>
            </button>

            <button
              type="button"
              (click)="sourceType.set('video')"
              class="min-h-8 min-w-8 p-3 rounded-xl border text-left transition cursor-pointer flex flex-col gap-1"
              [class.border-purple-500]="sourceType() === 'video'"
              [class.bg-purple-950]="sourceType() === 'video'"
              [class.border-slate-800]="sourceType() !== 'video'"
              [class.bg-slate-950]="sourceType() !== 'video'"
            >
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold uppercase tracking-wider text-purple-300">Path 2: Raw Video via FFmpeg</span>
                <span class="text-xs font-mono text-slate-300">.mp4 / .mkv / .mov</span>
              </div>
              <p class="text-xs text-slate-200">
                Extract candidate frames via FFmpeg scene filter, I-Frame keyframes, or window-peak interval sampling.
              </p>
            </button>
          </div>

          <!-- Core Identity & Triggerword Fields -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-semibold text-slate-300" for="ds-name-input">Dataset Name</label>
              <input
                id="ds-name-input"
                type="text"
                [ngModel]="datasetName()"
                (ngModelChange)="datasetName.set($event)"
                placeholder="e.g., Evelyn_Krea2_Character_v1"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-sm text-white focus:border-orange-500 focus:outline-none"
              />
            </div>

            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-semibold text-slate-300" for="ds-path-input">Source Path (under /data/input)</label>
              <input
                id="ds-path-input"
                type="text"
                [ngModel]="sourcePath()"
                (ngModelChange)="sourcePath.set($event)"
                placeholder="/data/input/my_folder_or_video.mp4"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-sm font-mono text-emerald-300 focus:border-orange-500 focus:outline-none"
              />
            </div>

            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-semibold text-slate-300" for="ds-trigger-input">
                LoRA Triggerword (Concept / Character / Style Token)
              </label>
              <input
                id="ds-trigger-input"
                type="text"
                [ngModel]="triggerWord()"
                (ngModelChange)="triggerWord.set($event)"
                placeholder="e.g., ohwx_woman or restrained_elegance"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-sky-500/50 text-sm font-mono text-sky-300 focus:border-sky-400 focus:outline-none"
              />
            </div>

            <div class="flex flex-col gap-1.5">
              <span class="text-xs font-semibold text-slate-300">Triggerword Placement Strategy</span>
              <div class="grid grid-cols-3 gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800">
                @for (pos of triggerPositions; track pos.id) {
                  <button
                    type="button"
                    (click)="triggerPosition.set(pos.id)"
                    class="min-h-8 min-w-8 py-1.5 px-2 rounded text-xs font-medium transition cursor-pointer"
                    [class.bg-sky-700]="triggerPosition() === pos.id"
                    [class.text-white]="triggerPosition() === pos.id"
                    [class.text-slate-300]="triggerPosition() !== pos.id"
                  >
                    {{ pos.label }}
                  </button>
                }
              </div>
            </div>

            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-semibold text-slate-300" for="ds-skill-select">Master VLM Skill Profile</label>
              <select
                id="ds-skill-select"
                [ngModel]="selectedSkillId()"
                (ngModelChange)="selectedSkillId.set($event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-sm text-white focus:border-orange-500 focus:outline-none"
              >
                @for (sk of api.skills(); track sk.id) {
                  <option [value]="sk.id">{{ sk.name }} (v{{ sk.version }} — {{ sk.min_words }}..{{ sk.max_words }}w)</option>
                }
                @if (api.skills().length === 0) {
                  <option value="krea2_character_v1">Krea 2 Character Training (v1)</option>
                  <option value="krea2_concept_v1">Krea 2 Concept Training (v1)</option>
                  <option value="krea2_style_v1">Krea 2 Style Training (v1)</option>
                }
              </select>
            </div>

            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-semibold text-slate-300" for="ds-target-input">
                Target Final Dataset Size (Stratification Goal)
              </label>
              <input
                id="ds-target-input"
                type="number"
                min="10"
                max="5000"
                [ngModel]="targetCount()"
                (ngModelChange)="targetCount.set(+$event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-sm font-mono text-white focus:border-orange-500 focus:outline-none"
              />
            </div>
          </div>

          <!-- Video FFmpeg Extraction Controls (Shown when sourceType === 'video') -->
          @if (sourceType() === 'video') {
            <div class="rounded-xl border border-purple-500/30 bg-slate-950 p-4 flex flex-col gap-4">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold uppercase tracking-wider text-purple-300">
                  FFmpeg Non-ML Frame Extraction Strategy
                </span>
                <code class="text-xs font-mono text-purple-200 bg-purple-950 px-2 py-0.5 rounded border border-purple-500/30">
                  {{ ffmpegCommandPreview() }}
                </code>
              </div>

              <div class="grid grid-cols-3 gap-2">
                <button
                  type="button"
                  (click)="extractionMode.set('scene')"
                  class="min-h-8 min-w-8 p-2.5 rounded-lg border text-left cursor-pointer transition"
                  [class.border-purple-400]="extractionMode() === 'scene'"
                  [class.bg-purple-950]="extractionMode() === 'scene'"
                  [class.border-slate-800]="extractionMode() !== 'scene'"
                  [class.bg-slate-900]="extractionMode() !== 'scene'"
                >
                  <div class="text-xs font-bold text-white">1. Scene Change</div>
                  <div class="text-xs text-slate-300 font-mono">select='gt(scene,{{ sceneThreshold() }})'</div>
                </button>

                <button
                  type="button"
                  (click)="extractionMode.set('iframe')"
                  class="min-h-8 min-w-8 p-2.5 rounded-lg border text-left cursor-pointer transition"
                  [class.border-purple-400]="extractionMode() === 'iframe'"
                  [class.bg-purple-950]="extractionMode() === 'iframe'"
                  [class.border-slate-800]="extractionMode() !== 'iframe'"
                  [class.bg-slate-900]="extractionMode() !== 'iframe'"
                >
                  <div class="text-xs font-bold text-white">2. I-Frame Keyframes</div>
                  <div class="text-xs text-slate-300 font-mono">-skip_frame nokey</div>
                </button>

                <button
                  type="button"
                  (click)="extractionMode.set('interval')"
                  class="min-h-8 min-w-8 p-2.5 rounded-lg border text-left cursor-pointer transition"
                  [class.border-purple-400]="extractionMode() === 'interval'"
                  [class.bg-purple-950]="extractionMode() === 'interval'"
                  [class.border-slate-800]="extractionMode() !== 'interval'"
                  [class.bg-slate-900]="extractionMode() !== 'interval'"
                >
                  <div class="text-xs font-bold text-white">3. Interval + Window-Peak</div>
                  <div class="text-xs text-slate-300 font-mono">Δt={{ intervalSeconds() }}s, peak M={{ windowPeak() }}</div>
                </button>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs">
                    <label for="scene-threshold-range" class="text-slate-300">Scene Threshold</label>
                    <span class="font-mono text-purple-300">{{ sceneThreshold() }}</span>
                  </div>
                  <input
                    id="scene-threshold-range"
                    type="range"
                    aria-label="Scene Threshold"
                    min="0.05"
                    max="0.60"
                    step="0.01"
                    [ngModel]="sceneThreshold()"
                    (ngModelChange)="sceneThreshold.set(+$event)"
                    class="accent-purple-500 cursor-pointer"
                  />
                </div>

                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs">
                    <label for="sample-interval-range" class="text-slate-300">Sample Interval (s)</label>
                    <span class="font-mono text-purple-300">{{ intervalSeconds() }}s</span>
                  </div>
                  <input
                    id="sample-interval-range"
                    type="range"
                    aria-label="Sample Interval"
                    min="0.1"
                    max="5.0"
                    step="0.1"
                    [ngModel]="intervalSeconds()"
                    (ngModelChange)="intervalSeconds.set(+$event)"
                    class="accent-purple-500 cursor-pointer"
                  />
                </div>

                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs">
                    <label for="window-peak-range" class="text-slate-300">Window-Peak Subsamples (M)</label>
                    <span class="font-mono text-purple-300">{{ windowPeak() }}</span>
                  </div>
                  <input
                    id="window-peak-range"
                    type="range"
                    aria-label="Window-Peak Subsamples"
                    min="1"
                    max="8"
                    step="1"
                    [ngModel]="windowPeak()"
                    (ngModelChange)="windowPeak.set(+$event)"
                    class="accent-purple-500 cursor-pointer"
                  />
                </div>
              </div>
            </div>
          }

          <!-- Perceptual Hash Clustering Slider -->
          <div class="rounded-xl border border-indigo-500/30 bg-slate-950 p-4 flex flex-col gap-2">
            <div class="flex items-center justify-between">
              <div>
                <label for="dedup-hamming-range" class="text-xs font-bold uppercase tracking-wider text-indigo-300">
                  O(N log N) Perceptual Hash (pHash + dHash + HSV) Deduplication
                </label>
                <p class="text-xs text-slate-300">
                  Groups frames within Hamming distance τ and automatically promotes argmax Q(I) as cluster representative
                </p>
              </div>
              <span class="px-2.5 py-1 rounded bg-indigo-950 text-indigo-300 border border-indigo-500/40 font-mono text-xs font-bold">
                τ = {{ dedupHammingThreshold() }} bits
              </span>
            </div>
            <input
              id="dedup-hamming-range"
              type="range"
              aria-label="Perceptual hash deduplication Hamming threshold"
              min="0"
              max="16"
              step="1"
              [ngModel]="dedupHammingThreshold()"
              (ngModelChange)="dedupHammingThreshold.set(+$event)"
              class="accent-indigo-500 cursor-pointer"
            />
          </div>

          <!-- Launch Action Buttons -->
          <div class="flex items-center justify-between pt-2 border-t border-slate-800">
            <div class="text-xs text-slate-300">
              @if (isSubmitting()) {
                <span class="text-amber-300 font-semibold animate-pulse">
                  Running FFmpeg extraction, 512px CV scoring & BK-tree clustering...
                </span>
              } @else {
                <span>384px WebP thumbnails & WAL state persisted on native Linux volume</span>
              }
            </div>

            <div class="flex items-center gap-3">
              @if (api.activeDataset()) {
                <button
                  type="button"
                  [disabled]="isSubmitting()"
                  (click)="reIngestActiveDataset()"
                  class="min-h-8 min-w-8 px-4 py-2 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 cursor-pointer disabled:opacity-50"
                >
                  Re-Ingest Active Dataset
                </button>
              }

              <button
                type="button"
                [disabled]="isSubmitting() || !datasetName().trim() || !sourcePath().trim()"
                (click)="createAndIngestDataset()"
                class="min-h-8 min-w-8 px-5 py-2.5 rounded-lg text-xs font-bold uppercase tracking-wider bg-orange-700 hover:bg-orange-600 text-white shadow-md cursor-pointer disabled:opacity-50 transition"
              >
                Create Dataset & Run Ingest →
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
})
export class Step1IngestComponent implements OnInit {
  readonly api = inject(StudioApiService);
  readonly stepNavigate = output<StudioStep>();

  readonly currentSubpath = signal<string>('');
  readonly fsState = signal<FsBrowseResponse | null>(null);
  readonly isSubmitting = signal<boolean>(false);

  // Form Signals
  readonly datasetName = signal<string>('Krea2_LoRA_Dataset_v1');
  readonly sourceType = signal<SourceType>('image_folder');
  readonly sourcePath = signal<string>('/data/input');
  readonly triggerWord = signal<string>('ohwx_subject');
  readonly triggerPosition = signal<TriggerPosition>('prefix');
  readonly selectedSkillId = signal<string>('krea2_concept_v1');
  readonly targetCount = signal<number>(150);

  // Extraction & Clustering Signals
  readonly extractionMode = signal<ExtractionMode>('scene');
  readonly sceneThreshold = signal<number>(0.15);
  readonly intervalSeconds = signal<number>(0.5);
  readonly windowPeak = signal<number>(3);
  readonly targetCandidates = signal<number>(600);
  readonly dedupHammingThreshold = signal<number>(6);

  readonly triggerPositions: Array<{ id: TriggerPosition; label: string }> = [
    { id: 'prefix', label: 'Prefix (Token 0)' },
    { id: 'suffix', label: 'Suffix (End)' },
    { id: 'natural_integration', label: 'Natural Prose' },
  ];

  readonly ffmpegCommandPreview = computed(() => {
    const mode = this.extractionMode();
    if (mode === 'scene') {
      return `ffmpeg -hwaccel auto -i <video> -vf "select='gt(scene,${this.sceneThreshold()})',showinfo" -vsync vfr`;
    }
    if (mode === 'iframe') {
      return `ffmpeg -skip_frame nokey -i <video> -vsync vfr -q:v 2`;
    }
    return `PyAV interval Δt=${this.intervalSeconds()}s (window-peak M=${this.windowPeak()} argmax Sharpness)`;
  });

  async ngOnInit(): Promise<void> {
    await this.browse('');
    const settings = this.api.settings();
    if (settings.trigger_word) {
      this.triggerWord.set(settings.trigger_word);
    }
    if (settings.trigger_position) {
      this.triggerPosition.set(settings.trigger_position);
    }
  }

  async browse(subpath: string): Promise<void> {
    try {
      const res = await this.api.browseFilesystem(subpath);
      this.fsState.set(res);
      this.currentSubpath.set(subpath);
      if (res?.current_path && this.sourcePath() === '/data/input') {
        this.sourcePath.set(res.current_path);
      }
    } catch {
      // Keep current state if /data/input is not yet mounted
    }
  }

  enterDirectory(dir: string): void {
    const cur = this.currentSubpath();
    const next = cur ? `${cur}/${dir}` : dir;
    void this.browse(next);
  }

  navigateUp(): void {
    const cur = this.currentSubpath();
    if (!cur) return;
    const parts = cur.split('/').filter(Boolean);
    parts.pop();
    void this.browse(parts.join('/'));
  }

  selectCurrentFolderAsSource(): void {
    const path = this.fsState()?.current_path || '/data/input';
    this.sourceType.set('image_folder');
    this.sourcePath.set(path);
    const leaf = path.split('/').filter(Boolean).pop();
    if (leaf && leaf !== 'input') {
      this.datasetName.set(`${leaf}_dataset`);
    }
  }

  selectSubfolderAsSource(dir: string): void {
    const base = this.fsState()?.current_path || '/data/input';
    this.sourceType.set('image_folder');
    this.sourcePath.set(`${base.replace(/\/+$/, '')}/${dir}`);
    this.datasetName.set(`${dir}_dataset`);
  }

  selectVideoAsSource(videoFile: string): void {
    const base = this.fsState()?.current_path || '/data/input';
    this.sourceType.set('video');
    this.sourcePath.set(`${base.replace(/\/+$/, '')}/${videoFile}`);
    const stem = videoFile.replace(/\.[^.]+$/, '');
    this.datasetName.set(`${stem}_video_lora`);
  }

  async selectExistingDataset(ds: StudioDataset): Promise<void> {
    this.api.activeDatasetId.set(ds.id);
    this.triggerWord.set(ds.trigger_word);
    this.triggerPosition.set(ds.trigger_position);
    this.dedupHammingThreshold.set(ds.dedup_hamming_threshold);
    await this.api.loadMediaItems(ds.id);
  }

  async openInTriage(ds: StudioDataset, ev: Event): Promise<void> {
    ev.stopPropagation();
    await this.selectExistingDataset(ds);
    this.stepNavigate.emit('triage');
  }

  async reclusterExisting(ds: StudioDataset, ev: Event): Promise<void> {
    ev.stopPropagation();
    this.isSubmitting.set(true);
    try {
      await this.api.reclusterDataset(ds.id, {
        hamming_threshold: this.dedupHammingThreshold(),
        auto_select_representatives: true,
      });
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async createAndIngestDataset(): Promise<void> {
    this.isSubmitting.set(true);
    try {
      await this.api.createDataset({
        name: this.datasetName().trim(),
        source_type: this.sourceType(),
        source_path: this.sourcePath().trim(),
        trigger_word: this.triggerWord().trim() || 'ohwx_subject',
        trigger_position: this.triggerPosition(),
        active_skill_id: this.selectedSkillId(),
        target_count: this.targetCount(),
        extraction_config: {
          mode: this.extractionMode(),
          scene_threshold: this.sceneThreshold(),
          interval_s: this.intervalSeconds(),
          window_peak: this.windowPeak(),
          target_candidates: this.targetCandidates(),
          dedup_hamming_threshold: this.dedupHammingThreshold(),
        },
        auto_ingest: true,
      });
      this.stepNavigate.emit('triage');
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async reIngestActiveDataset(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) return;
    this.isSubmitting.set(true);
    try {
      await this.api.reIngestDataset(ds.id, {
        mode: this.extractionMode(),
        scene_threshold: this.sceneThreshold(),
        interval_s: this.intervalSeconds(),
        window_peak: this.windowPeak(),
        target_candidates: this.targetCandidates(),
        dedup_hamming_threshold: this.dedupHammingThreshold(),
      });
      this.stepNavigate.emit('triage');
    } finally {
      this.isSubmitting.set(false);
    }
  }
}
