import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  OnInit,
  computed,
  effect,
  inject,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  CurationState,
  StudioMediaItem,
  StudioStep,
  TagCategory,
} from '../../models/studio.models';
import { StudioApiService } from '../../services/studio-api.service';

export interface QuickHotkeyTag {
  key: string;
  name: string;
  slug: string;
  category: TagCategory;
}

@Component({
  selector: 'app-step2-triage',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div data-testid="view-triage" class="flex flex-col h-full w-full overflow-hidden bg-slate-950 text-slate-100 select-none">
      <!-- Top Filter & Keyboard Ergonomics Toolbar -->
      <div class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 border-b border-slate-800 bg-slate-900 shrink-0">
        <!-- State Filter Pills -->
        <div class="flex flex-wrap items-center gap-1.5">
          @for (f of stateFilters; track f.id) {
            <button
              type="button"
              (click)="activeStateFilter.set(f.id)"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-md text-xs font-semibold transition cursor-pointer"
              [class.bg-orange-700]="activeStateFilter() === f.id"
              [class.text-white]="activeStateFilter() === f.id"
              [class.bg-slate-800]="activeStateFilter() !== f.id"
              [class.text-slate-200]="activeStateFilter() !== f.id"
            >
              {{ f.label }}
            </button>
          }

          <label class="flex items-center gap-1.5 ml-2 min-h-8 px-2 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              aria-label="Include Hidden Cluster Siblings"
              [ngModel]="showHiddenDuplicates()"
              (ngModelChange)="toggleDuplicates($event)"
              class="rounded accent-indigo-500"
            />
            <span>Include Hidden Cluster Siblings</span>
          </label>

          <div class="flex items-center gap-2 ml-3 text-xs">
            <label for="min-q-range" class="text-slate-300 font-semibold">Min Q:</label>
            <input
              id="min-q-range"
              type="range"
              aria-label="Minimum quality score filter"
              min="0"
              max="0.9"
              step="0.05"
              [ngModel]="minQualityFilter()"
              (ngModelChange)="minQualityFilter.set(+$event)"
              class="w-24 accent-emerald-500 cursor-pointer"
            />
            <span class="font-mono text-emerald-300 w-8">{{ minQualityFilter().toFixed(2) }}</span>
          </div>
        </div>

        <!-- Quick-Tag Hotkeys Strip (Keys 1-9) -->
        <div class="flex flex-wrap items-center gap-1">
          <span class="text-xs text-slate-300 mr-1 uppercase tracking-wider font-semibold">Hotkeys 1–9:</span>
          @for (qt of quickTags; track qt.key) {
            <button
              type="button"
              (click)="assignQuickTagToActive(qt)"
              class="min-h-8 min-w-8 px-2.5 py-1.5 rounded bg-slate-950 hover:bg-slate-800 border border-slate-700 text-xs flex items-center gap-1 cursor-pointer"
              [title]="'Press ' + qt.key + ' to toggle tag ' + qt.name + ' (' + qt.category + ')'"
            >
              <kbd class="text-orange-300 font-mono font-bold">{{ qt.key }}</kbd>
              <span class="text-slate-200">{{ qt.name }}</span>
            </button>
          }
        </div>

        <!-- Right Step CTA -->
        <div class="flex items-center gap-2">
          <button
            type="button"
            (click)="openClusterDrawerForActive()"
            class="min-h-8 min-w-8 px-3 py-1.5 rounded-md text-xs font-semibold bg-indigo-950 hover:bg-indigo-900 text-indigo-200 border border-indigo-500/40 cursor-pointer"
          >
            Cluster Compare (C)
          </button>
          <button
            type="button"
            (click)="stepNavigate.emit('tagging')"
            class="min-h-8 min-w-8 px-3 py-1.5 rounded-md text-xs font-bold bg-orange-700 hover:bg-orange-600 text-white cursor-pointer"
          >
            Next: Tagging & Stratification →
          </button>
        </div>
      </div>

      <!-- Main Split Content: Thumbnail Grid + Active Inspector / Cluster Drawer -->
      <div class="flex flex-1 min-h-0 overflow-hidden">
        <!-- Center High-Speed Thumbnail Grid -->
        <div class="flex-1 overflow-y-auto p-4">
          @if (filteredItems().length === 0) {
            <div class="h-full flex flex-col items-center justify-center text-center p-8 border border-dashed border-slate-800 rounded-xl">
              <p class="text-sm font-semibold text-slate-200">No media items match the current triage filter.</p>
              <p class="text-xs text-slate-300 mt-1">
                Ingest a dataset in Step 1 or lower the Min Q filter / enable hidden duplicates.
              </p>
            </div>
          } @else {
            <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5 gap-3">
              @for (item of filteredItems(); track item.id; let idx = $index) {
                <div
                  [attr.id]="'triage-card-' + item.id"
                  class="group relative rounded-xl overflow-hidden border bg-slate-900 flex flex-col transition-colors"
                  [class.ring-2]="selectedIndex() === idx"
                  [class.ring-orange-500]="selectedIndex() === idx"
                  [class.border-emerald-500]="item.curation_state === 'keep'"
                  [class.border-amber-400]="item.curation_state === 'star'"
                  [class.border-rose-500]="item.curation_state === 'reject' || item.curation_state === 'auto_rejected'"
                  [class.border-slate-800]="item.curation_state === 'candidate' || item.curation_state === 'duplicate_hidden'"
                >
                  <!-- Aspect-Preserving Thumbnail Container -->
                  <div class="relative aspect-video w-full bg-slate-950 overflow-hidden flex items-center justify-center">
                    <button
                      type="button"
                      (click)="selectIndex(idx)"
                      (dblclick)="openClusterComparison(item)"
                      [attr.aria-label]="'Select frame ' + item.source_filename"
                      [title]="item.source_filename"
                      class="w-full h-full block cursor-pointer focus:outline-none"
                    >
                      <img
                        [src]="item.thumbnail_url"
                        [alt]="item.source_filename"
                        width="640"
                        height="360"
                        class="w-full h-full object-cover"
                      />
                    </button>

                    <!-- Top-Left Badge: Curation State & Outlier / Manual Alert -->
                    <div class="absolute top-2 left-2 flex flex-col gap-1 pointer-events-none">
                      <span
                        class="px-2 py-0.5 rounded text-xs font-mono font-bold uppercase tracking-wider shadow border"
                        [class.bg-emerald-950]="item.curation_state === 'keep'"
                        [class.text-emerald-300]="item.curation_state === 'keep'"
                        [class.border-emerald-500/50]="item.curation_state === 'keep'"
                        [class.bg-amber-950]="item.curation_state === 'star'"
                        [class.text-amber-300]="item.curation_state === 'star'"
                        [class.border-amber-400/60]="item.curation_state === 'star'"
                        [class.bg-rose-950]="item.curation_state === 'reject' || item.curation_state === 'auto_rejected'"
                        [class.text-rose-300]="item.curation_state === 'reject' || item.curation_state === 'auto_rejected'"
                        [class.border-rose-500/50]="item.curation_state === 'reject' || item.curation_state === 'auto_rejected'"
                        [class.bg-slate-900]="item.curation_state === 'candidate' || item.curation_state === 'duplicate_hidden'"
                        [class.text-slate-200]="item.curation_state === 'candidate' || item.curation_state === 'duplicate_hidden'"
                        [class.border-slate-700]="item.curation_state === 'candidate' || item.curation_state === 'duplicate_hidden'"
                      >
                        @if (item.curation_state === 'star') {
                          ★ STAR
                        } @else if (item.curation_state === 'keep') {
                          ✓ KEEP
                        } @else if (item.curation_state === 'auto_rejected') {
                          ⛔ {{ item.reject_reason || 'GATE FAIL' }}
                        } @else {
                          {{ item.curation_state }}
                        }
                      </span>

                      @if (item.caption_status === 'needs_manual_caption') {
                        <span class="px-1.5 py-0.5 rounded text-xs font-bold bg-rose-800 text-white shadow">
                          NEEDS MANUAL
                        </span>
                      }
                      @if (item.is_outlier) {
                        <span class="px-1.5 py-0.5 rounded text-xs font-bold bg-amber-950 text-amber-200 border border-amber-500/50 shadow">
                          STAT OUTLIER
                        </span>
                      }
                    </div>

                    <!-- Top-Right Badge: Composite Mathematical Quality Score Q -->
                    <div class="absolute top-2 right-2 flex flex-col items-end gap-1 pointer-events-none">
                      <span
                        class="px-2 py-0.5 rounded text-xs font-mono font-bold shadow border"
                        [class.bg-emerald-950]="item.quality_score >= 0.75"
                        [class.text-emerald-300]="item.quality_score >= 0.75"
                        [class.border-emerald-500/50]="item.quality_score >= 0.75"
                        [class.bg-amber-950]="item.quality_score >= 0.5 && item.quality_score < 0.75"
                        [class.text-amber-300]="item.quality_score >= 0.5 && item.quality_score < 0.75"
                        [class.border-amber-500/50]="item.quality_score >= 0.5 && item.quality_score < 0.75"
                        [class.bg-rose-950]="item.quality_score < 0.5"
                        [class.text-rose-300]="item.quality_score < 0.5"
                        [class.border-rose-500/50]="item.quality_score < 0.5"
                        [title]="'Composite Quality Q = ' + item.quality_score.toFixed(3)"
                      >
                        Q {{ (item.quality_score * 100).toFixed(0) }}
                      </span>

                      @if (item.cluster_id !== null && item.cluster_id !== undefined) {
                        <span
                          class="px-1.5 py-0.5 rounded text-xs font-mono font-semibold border"
                          [class.bg-indigo-700]="item.cluster_size > 1"
                          [class.text-white]="item.cluster_size > 1"
                          [class.border-indigo-400]="item.cluster_size > 1"
                          [class.bg-slate-900]="item.cluster_size <= 1"
                          [class.text-slate-200]="item.cluster_size <= 1"
                          [class.border-slate-700]="item.cluster_size <= 1"
                        >
                          C#{{ item.cluster_id }} ({{ item.cluster_size }})
                        </span>
                      }
                    </div>

                    <!-- Bottom Glassmorphic Mathematical CV Bar -->
                    <div class="absolute bottom-0 inset-x-0 bg-slate-950 px-2 py-1 border-t border-slate-800 flex items-center justify-between text-xs font-mono text-slate-200 pointer-events-none">
                      <span [title]="'Tile Laplacian: ' + item.sharpness_laplacian.toFixed(1) + ' | Tenengrad: ' + item.sharpness_tenengrad.toFixed(1)">
                        Shp:{{ (item.sharpness_score * 100).toFixed(0) }}
                      </span>
                      <span [title]="'Mean Luma: ' + item.exposure_mean_luma.toFixed(0) + ' | Clipping: ' + (item.exposure_clipping_frac * 100).toFixed(1) + '%'">
                        Exp:{{ (item.exposure_score * 100).toFixed(0) }}
                      </span>
                      <span [title]="'Shannon Entropy: ' + item.entropy_shannon.toFixed(2) + ' bits'">
                        H:{{ item.entropy_shannon.toFixed(1) }}b
                      </span>
                    </div>
                  </div>

                  <!-- Card Footer: Filename, Timestamp & Assigned Tags -->
                  <div class="p-2 flex flex-col gap-1.5 bg-slate-900">
                    <div class="flex items-center justify-between text-xs font-mono text-slate-200">
                      <span class="truncate max-w-[140px]" [title]="item.source_filename">{{ item.source_filename }}</span>
                      @if (item.timestamp_s !== null && item.timestamp_s !== undefined) {
                        <span class="text-purple-300">{{ item.timestamp_s.toFixed(2) }}s</span>
                      } @else {
                        <span class="text-slate-300">{{ item.width }}×{{ item.height }}</span>
                      }
                    </div>

                    <!-- Tag Pills (Fixed Min-Height to Prevent Row Shift) -->
                    <div class="flex flex-wrap gap-1 min-h-6 items-center">
                      @for (t of item.tags.slice(0, 3); track t.slug) {
                        <span class="px-1.5 py-0.5 rounded bg-slate-950 text-sky-300 border border-slate-700 text-xs font-mono">
                          {{ t.name }}
                        </span>
                      }
                      @if (item.tags.length > 3) {
                        <span class="text-xs text-slate-300 font-mono">+{{ item.tags.length - 3 }}</span>
                      }
                    </div>

                    <!-- Quick Mouse Action Strip -->
                    <div class="grid grid-cols-4 gap-1 pt-1 border-t border-slate-800">
                      <button
                        type="button"
                        (click)="setTriageState(item, 'keep', $event)"
                        class="min-h-8 min-w-8 px-1.5 py-1.5 rounded text-xs font-bold bg-emerald-950 hover:bg-emerald-700 text-emerald-300 hover:text-white border border-emerald-500/40 cursor-pointer"
                        title="Keep (Space / Enter)"
                      >
                        Keep
                      </button>
                      <button
                        type="button"
                        (click)="setTriageState(item, 'star', $event)"
                        class="min-h-8 min-w-8 px-1.5 py-1.5 rounded text-xs font-bold bg-amber-950 hover:bg-amber-500 text-amber-300 hover:text-slate-950 border border-amber-500/40 cursor-pointer"
                        title="Star (S)"
                      >
                        Star
                      </button>
                      <button
                        type="button"
                        (click)="setTriageState(item, 'reject', $event)"
                        class="min-h-8 min-w-8 px-1.5 py-1.5 rounded text-xs font-bold bg-rose-950 hover:bg-rose-700 text-rose-300 hover:text-white border border-rose-500/40 cursor-pointer"
                        title="Reject (X / Backspace)"
                      >
                        Reject
                      </button>
                      <button
                        type="button"
                        (click)="openClusterComparison(item, $event)"
                        class="min-h-8 min-w-8 px-1.5 py-1.5 rounded text-xs font-bold bg-indigo-950 hover:bg-indigo-700 text-indigo-300 hover:text-white border border-indigo-500/40 cursor-pointer"
                        title="Compare Cluster (C)"
                      >
                        Cluster
                      </button>
                    </div>
                  </div>
                </div>
              }
            </div>
          }
        </div>

        <!-- Right Inspector Sidebar: Always Mounted on xl to Prevent Horizontal Layout Shift -->
        <aside class="w-80 border-l border-slate-800 bg-slate-900 p-4 flex flex-col gap-4 overflow-y-auto shrink-0 hidden xl:flex">
          @if (activeItem(); as sel) {
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-wider text-orange-300">
                Active Frame Telemetry
              </h3>
              <span class="text-xs font-mono text-slate-300">#{{ sel.id }}</span>
            </div>

            <div class="rounded-lg overflow-hidden border border-slate-800 bg-slate-950 aspect-video w-full flex items-center justify-center">
              <img
                [src]="sel.thumbnail_url"
                [alt]="sel.source_filename"
                width="640"
                height="360"
                class="w-full h-full object-contain"
              />
            </div>

            <div class="flex flex-col gap-2 text-xs font-mono bg-slate-950 p-3 rounded-lg border border-slate-800">
              <div class="flex justify-between">
                <span class="text-slate-300">Composite Q(I):</span>
                <span class="text-emerald-300 font-bold">{{ sel.quality_score.toFixed(4) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">Laplacian L_tile:</span>
                <span class="text-slate-100">{{ sel.sharpness_laplacian.toFixed(1) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">Tenengrad Sobel:</span>
                <span class="text-slate-100">{{ sel.sharpness_tenengrad.toFixed(1) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">Mean Luminance μ_I:</span>
                <span class="text-slate-100">{{ sel.exposure_mean_luma.toFixed(1) }} / 255</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">Clipping Fraction:</span>
                <span class="text-slate-100">{{ (sel.exposure_clipping_frac * 100).toFixed(2) }}%</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">Shannon Entropy H:</span>
                <span class="text-slate-100">{{ sel.entropy_shannon.toFixed(2) }} bits</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">64-bit pHash:</span>
                <span class="text-indigo-300">{{ sel.phash }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-300">64-bit dHash:</span>
                <span class="text-indigo-300">{{ sel.dhash }}</span>
              </div>
            </div>
          } @else {
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-wider text-orange-300">
                Active Frame Telemetry
              </h3>
              <span class="text-xs font-mono text-slate-300">—</span>
            </div>
            <div class="rounded-lg border border-slate-800 bg-slate-950 aspect-video w-full flex items-center justify-center text-xs text-slate-300">
              No frame selected
            </div>
          }

          <!-- Keyboard Cheat Card -->
          <div class="rounded-lg border border-slate-800 bg-slate-950 p-3 flex flex-col gap-1.5 text-xs">
            <div class="font-bold text-slate-200 uppercase tracking-wider text-xs">
              Zero-Friction Keyboard Controls
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Navigate Grid</span>
              <kbd class="font-mono text-slate-100">← ↑ ↓ →</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Keep & Advance</span>
              <kbd class="font-mono text-emerald-300">Space / Enter</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Reject & Advance</span>
              <kbd class="font-mono text-rose-300">X / Backspace</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Star Hero Frame</span>
              <kbd class="font-mono text-amber-300">S</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Quick-Tag Item</span>
              <kbd class="font-mono text-sky-300">1 – 9</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Cluster Drawer</span>
              <kbd class="font-mono text-indigo-300">C</kbd>
            </div>
            <div class="flex justify-between text-slate-300">
              <span>Force Re-Caption</span>
              <kbd class="font-mono text-orange-300">R</kbd>
            </div>
          </div>
        </aside>
      </div>

      <!-- Expandable Bottom Drawer: Near-Duplicate Cluster Comparison (Toggled via 'C') -->
      @if (clusterDrawerOpen()) {
        <div class="h-72 border-t-2 border-indigo-500 bg-slate-900 p-4 flex flex-col gap-3 shrink-0 shadow-2xl">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <span class="px-2.5 py-1 rounded bg-indigo-700 text-white text-xs font-mono font-bold">
                Cluster #{{ activeClusterId() }} Comparison
              </span>
              <span class="text-xs text-slate-200">
                Comparing winning representative (<code class="text-emerald-300">argmax Q(I)</code>) side-by-side with {{ clusterMembers().length }} near-duplicate sibling frames
              </span>
            </div>
            <button
              type="button"
              (click)="clusterDrawerOpen.set(false)"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 cursor-pointer"
            >
              Close Drawer (C / Esc)
            </button>
          </div>

          <div class="flex-1 flex items-center gap-4 overflow-x-auto pb-1">
            @for (member of clusterMembers(); track member.id) {
              <div
                class="w-60 h-full rounded-xl border p-2.5 flex flex-col justify-between shrink-0 transition"
                [class.border-emerald-400]="member.is_cluster_representative"
                [class.bg-emerald-950]="member.is_cluster_representative"
                [class.border-slate-800]="!member.is_cluster_representative"
                [class.bg-slate-950]="!member.is_cluster_representative"
              >
                <div class="flex items-center justify-between text-xs font-mono">
                  @if (member.is_cluster_representative) {
                    <span class="px-2 py-0.5 rounded bg-emerald-700 text-white font-bold text-xs">
                      👑 REPRESENTATIVE
                    </span>
                  } @else {
                    <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-xs">
                      {{ member.curation_state }}
                    </span>
                  }
                  <span class="text-emerald-300 font-bold">Q={{ member.quality_score.toFixed(3) }}</span>
                </div>

                <div class="my-1.5 h-28 bg-slate-950 rounded overflow-hidden flex items-center justify-center">
                  <img
                    [src]="member.thumbnail_url"
                    [alt]="member.source_filename"
                    width="640"
                    height="360"
                    class="h-full w-full object-contain"
                  />
                </div>

                <div class="flex items-center justify-between text-xs font-mono text-slate-300">
                  <span>Shp: {{ (member.sharpness_score * 100).toFixed(0) }}</span>
                  <span>Exp: {{ (member.exposure_score * 100).toFixed(0) }}</span>
                  <span>pHash: {{ member.phash.slice(0, 8) }}</span>
                </div>

                @if (!member.is_cluster_representative) {
                  <button
                    type="button"
                    (click)="promoteToRepresentative(member)"
                    class="mt-1.5 w-full min-h-8 min-w-8 py-1.5 rounded bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-semibold cursor-pointer transition"
                  >
                    Promote to Representative
                  </button>
                } @else {
                  <div class="mt-1.5 w-full py-1.5 text-center text-xs font-semibold text-emerald-300">
                    Active Cluster Winner
                  </div>
                }
              </div>
            }
          </div>
        </div>
      }
    </div>
  `,
})
export class Step2TriageComponent implements OnInit {
  readonly api = inject(StudioApiService);
  readonly stepNavigate = output<StudioStep>();

  readonly activeStateFilter = signal<'all' | 'keep_star' | 'candidate' | 'rejected'>('all');
  readonly showHiddenDuplicates = signal<boolean>(false);
  readonly minQualityFilter = signal<number>(0.0);
  readonly selectedIndex = signal<number>(0);

  // Near-Duplicate Cluster Drawer State
  readonly clusterDrawerOpen = signal<boolean>(false);
  readonly activeClusterId = signal<number | null>(null);
  readonly clusterMembers = signal<StudioMediaItem[]>([]);

  constructor() {
    effect(() => {
      const ds = this.api.activeDataset();
      if (!ds) return;
      untracked(() => {
        if (this.api.mediaItems().length === 0 && !this.api.loadingItems()) {
          void this.api.loadMediaItems(ds.id, { include_duplicates: true });
        }
      });
    });
  }

  readonly stateFilters: Array<{
    id: 'all' | 'keep_star' | 'candidate' | 'rejected';
    label: string;
  }> = [
    { id: 'all', label: 'All Active' },
    { id: 'keep_star', label: 'Kept & Starred' },
    { id: 'candidate', label: 'Candidates' },
    { id: 'rejected', label: 'Rejected / Gate Failed' },
  ];

  readonly quickTags: QuickHotkeyTag[] = [
    { key: '1', name: 'close_up', slug: 'close_up', category: 'shot_scale' },
    { key: '2', name: 'medium_shot', slug: 'medium_shot', category: 'shot_scale' },
    { key: '3', name: 'full_body', slug: 'full_body', category: 'shot_scale' },
    { key: '4', name: 'chiaroscuro', slug: 'chiaroscuro_high_contrast', category: 'lighting' },
    { key: '5', name: 'low_key', slug: 'low_key_dark', category: 'lighting' },
    { key: '6', name: 'high_key', slug: 'high_key_bright', category: 'lighting' },
    { key: '7', name: 'eye_contact', slug: 'direct_gaze', category: 'expression' },
    { key: '8', name: 'profile_pose', slug: 'profile_pose', category: 'pose' },
    { key: '9', name: 'bokeh_dof', slug: 'close_focus_bokeh', category: 'style_element' },
  ];

  readonly filteredItems = computed<StudioMediaItem[]>(() => {
    const all = this.api.mediaItems();
    const filter = this.activeStateFilter();
    const includeDup = this.showHiddenDuplicates();
    const minQ = this.minQualityFilter();

    return all.filter((item) => {
      if (!includeDup && item.curation_state === 'duplicate_hidden') {
        return false;
      }
      if (item.quality_score < minQ) {
        return false;
      }
      if (filter === 'keep_star') {
        return item.curation_state === 'keep' || item.curation_state === 'star';
      }
      if (filter === 'candidate') {
        return item.curation_state === 'candidate';
      }
      if (filter === 'rejected') {
        return item.curation_state === 'reject' || item.curation_state === 'auto_rejected';
      }
      return true;
    });
  });

  readonly activeItem = computed<StudioMediaItem | null>(() => {
    const list = this.filteredItems();
    const idx = this.selectedIndex();
    if (list.length === 0) return null;
    const clamped = Math.max(0, Math.min(idx, list.length - 1));
    return list[clamped] ?? null;
  });

  async ngOnInit(): Promise<void> {
    const ds = this.api.activeDataset();
    if (ds && this.api.mediaItems().length === 0) {
      await this.api.loadMediaItems(ds.id, { include_duplicates: true });
    }
  }

  selectIndex(idx: number): void {
    const list = this.filteredItems();
    if (list.length === 0) return;
    const nextIdx = Math.max(0, Math.min(idx, list.length - 1));
    this.selectedIndex.set(nextIdx);
    const item = list[nextIdx];
    if (item && typeof document !== 'undefined') {
      document.getElementById(`triage-card-${item.id}`)?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }
  }

  toggleDuplicates(show: boolean): void {
    this.showHiddenDuplicates.set(show);
  }

  async setTriageState(
    item: StudioMediaItem,
    state: CurationState,
    ev?: Event,
  ): Promise<void> {
    ev?.stopPropagation();
    const ds = this.api.activeDataset();
    if (!ds) return;
    await this.api.batchTriage(ds.id, [item.id], state);
  }

  async assignQuickTagToActive(qt: QuickHotkeyTag): Promise<void> {
    const item = this.activeItem();
    const ds = this.api.activeDataset();
    if (!item || !ds) return;
    const hasTag = item.tags?.some((t) => t.slug === qt.slug);
    if (hasTag) {
      await this.api.batchTagItems(ds.id, {
        item_ids: [item.id],
        remove_tag_slugs: [qt.slug],
      });
      this.api.showToast(`Removed tag "${qt.name}" from #${item.id}`, 'info');
    } else {
      await this.api.batchTagItems(ds.id, {
        item_ids: [item.id],
        add_tags: [{ name: qt.name, slug: qt.slug, category: qt.category }],
      });
      this.api.showToast(`Tagged #${item.id} with "${qt.name}" [Key ${qt.key}]`, 'success');
    }
  }

  async openClusterDrawerForActive(): Promise<void> {
    const item = this.activeItem();
    if (!item) return;
    await this.openClusterComparison(item);
  }

  async openClusterComparison(item: StudioMediaItem, ev?: Event): Promise<void> {
    ev?.stopPropagation();
    const ds = this.api.activeDataset();
    if (!ds || !item.cluster_id) {
      this.api.showToast('Item does not belong to a multi-frame cluster', 'info');
      return;
    }
    if (this.clusterDrawerOpen() && this.activeClusterId() === item.cluster_id) {
      this.clusterDrawerOpen.set(false);
      return;
    }
    this.activeClusterId.set(item.cluster_id);
    this.clusterDrawerOpen.set(true);
    const res = await this.api.loadMediaItems(ds.id, {
      cluster_id: item.cluster_id,
      include_duplicates: true,
    });
    this.clusterMembers.set(res.items);
  }

  async promoteToRepresentative(member: StudioMediaItem): Promise<void> {
    const ds = this.api.activeDataset();
    const cid = this.activeClusterId();
    if (!ds || !cid) return;
    await this.api.swapClusterRepresentative(ds.id, cid, member.id);
    const res = await this.api.loadMediaItems(ds.id, {
      cluster_id: cid,
      include_duplicates: true,
    });
    this.clusterMembers.set(res.items);
  }

  // =========================================================================
  // Zero-Friction Keyboard-First Navigation (@HostListener)
  // =========================================================================
  private readonly arrowStepByKey: Record<string, number> = {
    ArrowRight: 1,
    ArrowLeft: -1,
    ArrowDown: 4,
    ArrowUp: -4,
  };

  private readonly stateByKey: Record<string, CurationState> = {
    ' ': 'keep',
    Enter: 'keep',
    Backspace: 'reject',
    x: 'reject',
    X: 'reject',
    s: 'star',
    S: 'star',
  };

  private isEditableTarget(target: HTMLElement | null): boolean {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
  }

  @HostListener('window:keydown', ['$event'])
  onWindowKeydown(ev: KeyboardEvent): void {
    if (this.isEditableTarget(ev.target as HTMLElement | null)) return;
    if (this.filteredItems().length === 0) return;

    const delta = this.arrowStepByKey[ev.key];
    if (delta !== undefined) {
      ev.preventDefault();
      this.selectIndex(this.selectedIndex() + delta);
      return;
    }

    const nextState = this.stateByKey[ev.key];
    const item = this.activeItem();
    if (nextState && item) {
      ev.preventDefault();
      void this.setTriageState(item, nextState);
      this.selectIndex(this.selectedIndex() + 1);
      return;
    }

    this.handleSecondaryHotkey(ev, item);
  }

  private handleSecondaryHotkey(ev: KeyboardEvent, item: StudioMediaItem | null): void {
    const lower = ev.key.toLowerCase();
    if (lower === 'c' && item) {
      ev.preventDefault();
      void this.openClusterComparison(item);
      return;
    }
    if (lower === 'r' && item) {
      ev.preventDefault();
      const ds = this.api.activeDataset();
      if (ds) void this.api.runDatasetCaptioning(ds.id, { only_uncaptioned: false });
      return;
    }
    if (ev.key === 'Escape' && this.clusterDrawerOpen()) {
      ev.preventDefault();
      this.clusterDrawerOpen.set(false);
      return;
    }
    const qt = this.quickTags.find((q) => q.key === ev.key);
    if (qt) {
      ev.preventDefault();
      void this.assignQuickTagToActive(qt);
    }
  }
}
