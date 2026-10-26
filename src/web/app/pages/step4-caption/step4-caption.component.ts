import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  DiffToken,
  RejectionLogEntry,
  StudioMediaItem,
  StudioSkill,
  StudioStep,
  TriggerPosition,
} from '../../models/studio.models';
import {
  StudioApiService,
  computeCaptionWordDiff,
} from '../../services/studio-api.service';

export interface CaptionHighlightSegment {
  text: string;
  isTrigger: boolean;
}

@Component({
  selector: 'app-step4-caption',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div data-testid="view-captioning" class="flex flex-col h-full w-full overflow-hidden bg-slate-950 text-slate-100">
      <!-- Top Cascade Job Bar & Filter Tabs -->
      <div class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 border-b border-slate-800 bg-slate-900/95 shrink-0">
        <!-- Filter Tabs including "Needs Manual Caption" -->
        <div class="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            (click)="captionFilter.set('all_kept')"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold transition cursor-pointer"
            [class.bg-orange-600]="captionFilter() === 'all_kept'"
            [class.text-white]="captionFilter() === 'all_kept'"
            [class.bg-slate-800]="captionFilter() !== 'all_kept'"
            [class.text-slate-300]="captionFilter() !== 'all_kept'"
          >
            All Kept ({{ allKeptItems().length }})
          </button>

          <button
            type="button"
            (click)="captionFilter.set('captioned')"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold transition cursor-pointer"
            [class.bg-emerald-600]="captionFilter() === 'captioned'"
            [class.text-white]="captionFilter() === 'captioned'"
            [class.bg-slate-800]="captionFilter() !== 'captioned'"
            [class.text-slate-300]="captionFilter() !== 'captioned'"
          >
            ✓ Captioned ({{ captionedCount() }})
          </button>

          <button
            type="button"
            (click)="captionFilter.set('uncaptioned')"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold transition cursor-pointer"
            [class.bg-sky-600]="captionFilter() === 'uncaptioned'"
            [class.text-white]="captionFilter() === 'uncaptioned'"
            [class.bg-slate-800]="captionFilter() !== 'uncaptioned'"
            [class.text-slate-300]="captionFilter() !== 'uncaptioned'"
          >
            Uncaptioned ({{ uncaptionedCount() }})
          </button>

          <!-- Dedicated "Needs Manual Caption" Alert Filter Tab -->
          <button
            type="button"
            (click)="captionFilter.set('needs_manual')"
            class="px-3 py-1.5 rounded-lg text-xs font-bold transition cursor-pointer flex items-center gap-1.5 border"
            [class.bg-rose-600]="captionFilter() === 'needs_manual'"
            [class.text-white]="captionFilter() === 'needs_manual'"
            [class.border-rose-400]="captionFilter() === 'needs_manual'"
            [class.bg-rose-950/70]="captionFilter() !== 'needs_manual'"
            [class.text-rose-300]="captionFilter() !== 'needs_manual'"
            [class.border-rose-500/40]="captionFilter() !== 'needs_manual'"
          >
            <span>🚨 Needs Manual Caption</span>
            <span class="px-1.5 py-0.2 rounded-full bg-rose-900 text-white font-mono text-[11px]">
              {{ needsManualItems().length }}
            </span>
          </button>
        </div>

        <!-- 3-Tier OpenRouter Cascade Runner Controls -->
        <div class="flex flex-wrap items-center gap-2">
          <select
            [ngModel]="selectedSkillId()"
            (ngModelChange)="selectedSkillId.set($event)"
            aria-label="Master Skill Profile"
            class="px-2.5 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs text-white"
          >
            @for (sk of api.skills(); track sk.id) {
              <option [value]="sk.id">{{ sk.name }} (v{{ sk.version }})</option>
            }
          </select>

          <input
            type="text"
            [ngModel]="classNoun()"
            (ngModelChange)="classNoun.set($event)"
            placeholder="Class noun (e.g. woman)"
            aria-label="Subject class noun"
            class="w-28 px-2.5 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-slate-200"
          />

          <label class="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              [ngModel]="onlyUncaptioned()"
              (ngModelChange)="onlyUncaptioned.set($event)"
              class="accent-orange-500"
            />
            <span>Only Uncaptioned</span>
          </label>

          <button
            type="button"
            [disabled]="isCaptioningJobRunning()"
            (click)="launchCascadeCaptionJob()"
            class="px-3.5 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider bg-indigo-600 hover:bg-indigo-500 text-white shadow cursor-pointer disabled:opacity-50"
          >
            @if (isCaptioningJobRunning()) {
              Running 3-Tier Cascade...
            } @else {
              🚀 Run OpenRouter 3-Tier Cascade
            }
          </button>

          <button
            type="button"
            (click)="stepNavigate.emit('export_rag')"
            class="px-3 py-1.5 rounded-lg text-xs font-bold bg-orange-600 hover:bg-orange-500 text-white cursor-pointer"
          >
            Step 5: Export & RAG →
          </button>
        </div>
      </div>

      <!-- Main Split-View Workspace -->
      <div class="flex flex-1 min-h-0 overflow-hidden">
        <!-- Far-Left Filmstrip Navigation Rail -->
        <aside class="w-48 border-r border-slate-800 bg-slate-900/90 overflow-y-auto p-2 flex flex-col gap-2 shrink-0">
          @for (item of filteredItems(); track item.id; let idx = $index) {
            <div
              (click)="selectItemIndex(idx)"
              class="p-1.5 rounded-lg border flex items-center gap-2 cursor-pointer transition"
              [class.border-orange-500]="selectedIndex() === idx"
              [class.bg-slate-800]="selectedIndex() === idx"
              [class.border-slate-800]="selectedIndex() !== idx"
              [class.bg-slate-950/60]="selectedIndex() !== idx"
            >
              <img
                [src]="item.thumbnail_url"
                [alt]="item.source_filename"
                class="w-12 h-12 rounded object-cover bg-slate-950 shrink-0"
              />
              <div class="flex flex-col min-w-0 flex-1">
                <span class="text-[11px] font-mono text-slate-200 truncate">#{{ item.id }} {{ item.source_filename }}</span>
                <span
                  class="text-[10px] font-mono uppercase font-bold"
                  [class.text-emerald-400]="item.caption_status === 'vlm_accepted' || item.caption_status === 'manual' || item.caption_status === 'approved'"
                  [class.text-rose-400]="item.caption_status === 'needs_manual_caption'"
                  [class.text-slate-400]="item.caption_status === 'uncaptioned'"
                >
                  {{ item.caption_status }}
                </span>
                @if (item.active_caption_layer) {
                  <span class="text-[9px] font-mono text-indigo-300 truncate">
                    {{ item.active_caption_layer }}
                  </span>
                }
              </div>
            </div>
          }
        </aside>

        @if (activeItem(); as item) {
          <!-- Left Pane: High-Res Image Preview with Zoom/Pan & Quality Breakdown -->
          <div class="w-5/12 border-r border-slate-800 bg-slate-950 flex flex-col min-h-0">
            <!-- Zoom & Pan Controls Toolbar -->
            <div class="flex items-center justify-between px-3 py-2 border-b border-slate-800 bg-slate-900/70 text-xs">
              <div class="flex items-center gap-2 font-mono">
                <span class="text-slate-300 font-bold">#{{ item.id }}</span>
                <span class="text-slate-400">{{ item.width }}×{{ item.height }}</span>
                <span class="text-emerald-400">Q={{ item.quality_score.toFixed(2) }}</span>
              </div>

              <div class="flex items-center gap-1.5">
                <button
                  type="button"
                  (click)="adjustZoom(-0.5)"
                  class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 font-mono cursor-pointer"
                >
                  -
                </button>
                <span class="font-mono text-xs text-orange-300 w-12 text-center">{{ zoomLevel().toFixed(1) }}×</span>
                <button
                  type="button"
                  (click)="adjustZoom(0.5)"
                  class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 font-mono cursor-pointer"
                >
                  +
                </button>
                <button
                  type="button"
                  (click)="resetZoomPan()"
                  class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] cursor-pointer"
                >
                  Reset 1:1
                </button>
              </div>
            </div>

            <!-- Interactive Pan/Zoom High-Res Viewport -->
            <div
              class="flex-1 relative overflow-hidden bg-slate-950 flex items-center justify-center cursor-grab active:cursor-grabbing"
              (mousedown)="startPan($event)"
              (mousemove)="movePan($event)"
              (mouseup)="endPan()"
              (mouseleave)="endPan()"
            >
              <img
                [src]="item.full_url"
                [alt]="item.source_filename"
                draggable="false"
                class="max-h-full max-w-full object-contain transition-transform duration-75 select-none"
                [style.transform]="'scale(' + zoomLevel() + ') translate(' + panX() + 'px, ' + panY() + 'px)'"
              />
            </div>

            <!-- Bottom Mathematical Quality & Confirmed Tags Strip -->
            <div class="p-3 border-t border-slate-800 bg-slate-900/90 flex flex-col gap-2">
              <div class="grid grid-cols-4 gap-2 text-[11px] font-mono bg-slate-950 p-2 rounded border border-slate-800">
                <div>
                  <span class="text-slate-400 block">Sharpness</span>
                  <span class="text-slate-200 font-bold">{{ (item.sharpness_score * 100).toFixed(0) }}%</span>
                </div>
                <div>
                  <span class="text-slate-400 block">Exposure</span>
                  <span class="text-slate-200 font-bold">{{ (item.exposure_score * 100).toFixed(0) }}%</span>
                </div>
                <div>
                  <span class="text-slate-400 block">Entropy</span>
                  <span class="text-slate-200 font-bold">{{ item.entropy_shannon.toFixed(1) }}b</span>
                </div>
                <div>
                  <span class="text-slate-400 block">pHash</span>
                  <span class="text-indigo-300 font-bold">{{ item.phash.slice(0, 8) }}</span>
                </div>
              </div>

              <div class="flex flex-wrap items-center gap-1">
                <span class="text-[10px] font-bold uppercase text-slate-400 mr-1">Grounding Tags:</span>
                @for (t of item.tags; track t.slug) {
                  <span class="px-2 py-0.5 rounded bg-slate-800 text-sky-300 border border-slate-700 text-[10px] font-mono">
                    {{ t.category }}:{{ t.name }}
                  </span>
                }
                @if (!item.tags || item.tags.length === 0) {
                  <span class="text-[11px] text-slate-400 italic">No tags assigned</span>
                }
              </div>
            </div>
          </div>

          <!-- Right Pane: Live Caption Editor, Triggerword Highlighting, Diff View & Rejection Audit -->
          <div class="flex-1 flex flex-col min-h-0 overflow-y-auto p-4 gap-4 bg-slate-900/60">
            <!-- Header Row: Model Origin Badge & Triggerword Badge -->
            <div class="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
              <!-- Model Origin Badge -->
              <div class="flex items-center gap-2">
                <span
                  class="px-3 py-1 rounded-full text-xs font-mono font-bold border shadow-xs"
                  [class.bg-emerald-950/90]="item.active_caption_layer === 'primary'"
                  [class.text-emerald-300]="item.active_caption_layer === 'primary'"
                  [class.border-emerald-500/40]="item.active_caption_layer === 'primary'"
                  [class.bg-amber-950/90]="item.active_caption_layer === 'fallback_1'"
                  [class.text-amber-300]="item.active_caption_layer === 'fallback_1'"
                  [class.border-amber-500/40]="item.active_caption_layer === 'fallback_1'"
                  [class.bg-purple-950/90]="item.active_caption_layer === 'fallback_2'"
                  [class.text-purple-300]="item.active_caption_layer === 'fallback_2'"
                  [class.border-purple-500/40]="item.active_caption_layer === 'fallback_2'"
                  [class.bg-sky-950/90]="item.active_caption_layer === 'manual'"
                  [class.text-sky-300]="item.active_caption_layer === 'manual'"
                  [class.border-sky-500/40]="item.active_caption_layer === 'manual'"
                  [class.bg-rose-950/90]="item.caption_status === 'needs_manual_caption'"
                  [class.text-rose-300]="item.caption_status === 'needs_manual_caption'"
                  [class.border-rose-500/40]="item.caption_status === 'needs_manual_caption'"
                >
                  {{ modelOriginLabel() }}
                </span>

                <!-- Triggerword Status Badge -->
                <span
                  class="px-2.5 py-1 rounded-md text-xs font-mono border"
                  [class.bg-sky-500/20]="hasTriggerWordInDraft()"
                  [class.text-sky-300]="hasTriggerWordInDraft()"
                  [class.border-sky-400/50]="hasTriggerWordInDraft()"
                  [class.bg-rose-950/80]="!hasTriggerWordInDraft()"
                  [class.text-rose-300]="!hasTriggerWordInDraft()"
                  [class.border-rose-500/40]="!hasTriggerWordInDraft()"
                >
                  Trigger: "{{ activeTriggerWord() }}" ({{ activeTriggerPosition() }})
                  {{ hasTriggerWordInDraft() ? '✓' : '⚠️ Missing' }}
                </span>
              </div>

              <!-- Toggle Diff View Button -->
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  (click)="showDiffView.set(!showDiffView())"
                  class="px-2.5 py-1 rounded text-xs font-semibold border cursor-pointer transition"
                  [class.bg-indigo-600]="showDiffView()"
                  [class.text-white]="showDiffView()"
                  [class.border-indigo-400]="showDiffView()"
                  [class.bg-slate-800]="!showDiffView()"
                  [class.text-slate-300]="!showDiffView()"
                  [class.border-slate-700]="!showDiffView()"
                >
                  ⇄ Live Word Diff View
                </button>
              </div>
            </div>

            <!-- Live Word & Token Budget Meter -->
            <div class="rounded-lg border border-slate-800 bg-slate-950 p-3 flex flex-col gap-1.5">
              <div class="flex items-center justify-between text-xs font-mono">
                <span class="text-slate-300">
                  Word Count:
                  <strong
                    [class.text-emerald-400]="isWordCountInBudget()"
                    [class.text-amber-400]="!isWordCountInBudget()"
                  >
                    {{ draftWordCount() }} words
                  </strong>
                  (Skill Target: {{ activeSkill()?.min_words ?? 80 }}–{{ activeSkill()?.max_words ?? 160 }} words)
                </span>
                <span class="text-slate-400">
                  Est. BPE Tokens: <strong class="text-sky-300">~{{ draftTokenCount() }} tokens</strong>
                </span>
              </div>
              <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                <div
                  class="h-full transition-all"
                  [class.bg-emerald-500]="isWordCountInBudget()"
                  [class.bg-amber-500]="!isWordCountInBudget()"
                  [style.width.%]="budgetProgressPercent()"
                ></div>
              </div>
            </div>

            <!-- Editable Caption Textarea -->
            <div class="flex flex-col gap-1.5">
              <label class="text-xs font-bold uppercase tracking-wider text-slate-300" for="caption-editor-textarea">
                Training Caption Prose Editor
              </label>
              <textarea
                id="caption-editor-textarea"
                rows="6"
                [ngModel]="draftCaption()"
                (ngModelChange)="draftCaption.set($event)"
                placeholder="Write or edit the training caption here. Triggerword will be automatically verified/injected on save..."
                class="w-full p-3.5 rounded-xl bg-slate-950 border border-slate-700 text-sm leading-relaxed text-slate-100 focus:border-orange-500 focus:outline-none font-sans"
              ></textarea>
            </div>

            <!-- Live Triggerword Highlighted Preview -->
            <div class="rounded-xl border border-slate-800 bg-slate-950/90 p-3.5 flex flex-col gap-1.5">
              <div class="text-[11px] font-bold uppercase tracking-wider text-sky-400">
                Live Triggerword Token Highlight Preview
              </div>
              <div class="text-xs leading-relaxed text-slate-200">
                @for (seg of highlightedSegments(); track $index) {
                  @if (seg.isTrigger) {
                    <span class="px-1.5 py-0.5 mx-0.5 rounded bg-sky-500/25 text-sky-300 border border-sky-400/60 font-mono font-bold">
                      {{ seg.text }}
                    </span>
                  } @else {
                    <span>{{ seg.text }}</span>
                  }
                }
              </div>
            </div>

            <!-- Optional Word-Level Diff View -->
            @if (showDiffView()) {
              <div class="rounded-xl border border-indigo-500/40 bg-indigo-950/15 p-3.5 flex flex-col gap-2">
                <div class="flex items-center justify-between text-xs">
                  <span class="font-bold uppercase tracking-wider text-indigo-300">
                    Word-Level Diff (Saved VLM Caption vs. Current Draft)
                  </span>
                  <div class="flex items-center gap-3 font-mono text-[11px]">
                    <span class="text-emerald-400">+ Added</span>
                    <span class="text-rose-400 line-through">- Removed</span>
                  </div>
                </div>
                <div class="text-xs leading-relaxed bg-slate-950 p-3 rounded-lg border border-slate-800">
                  @for (tok of diffTokens(); track $index) {
                    @if (tok.type === 'added') {
                      <span class="bg-emerald-500/25 text-emerald-300 px-1 rounded font-semibold mr-1">
                        +{{ tok.text }}
                      </span>
                    } @else if (tok.type === 'removed') {
                      <span class="bg-rose-500/25 text-rose-300 line-through px-1 rounded mr-1">
                        -{{ tok.text }}
                      </span>
                    } @else {
                      <span class="text-slate-300 mr-1">{{ tok.text }}</span>
                    }
                  }
                </div>
              </div>
            }

            <!-- Save / Approve & Next Actions -->
            <div class="flex items-center justify-between pt-2 border-t border-slate-800">
              <label class="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  [ngModel]="enforceTriggerOnSave()"
                  (ngModelChange)="enforceTriggerOnSave.set($event)"
                  class="accent-sky-500"
                />
                <span>Enforce Triggerword ({{ activeTriggerPosition() }}) on Save</span>
              </label>

              <div class="flex items-center gap-2">
                <button
                  type="button"
                  (click)="revertDraft()"
                  class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 cursor-pointer"
                >
                  Revert
                </button>
                <button
                  type="button"
                  (click)="saveAndApproveCaption(true)"
                  class="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white shadow cursor-pointer"
                >
                  ✓ Save & Approve (Next Item)
                </button>
              </div>
            </div>

            <!-- Rejection-Guard Audit Log Inspector -->
            <div class="rounded-xl border border-slate-800 bg-slate-950 p-3.5 flex flex-col gap-2.5">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold uppercase tracking-wider text-rose-400">
                  🛡️ Rejection-Guard Cascade Audit Log (Item #{{ item.id }})
                </span>
                <span class="text-[11px] font-mono text-slate-400">
                  {{ activeItemRejections().length }} rejected attempts logged
                </span>
              </div>

              @if (activeItemRejections().length === 0) {
                <div class="text-xs text-slate-400 py-2">
                  No VLM refusals, preambles, or truncations recorded for this item.
                </div>
              } @else {
                <div class="flex flex-col gap-2">
                  @for (log of activeItemRejections(); track log.id) {
                    <div class="p-2.5 rounded-lg border border-rose-500/30 bg-rose-950/20 flex flex-col gap-1 text-xs">
                      <div class="flex items-center justify-between font-mono">
                        <span class="text-rose-300 font-bold uppercase">
                          {{ log.cascade_layer }} — {{ log.model_attempted }}
                        </span>
                        <span class="px-2 py-0.5 rounded bg-rose-900/80 text-rose-200 text-[10px]">
                          {{ log.rejection_type }}
                        </span>
                      </div>
                      <div class="text-amber-200 text-[11px]">
                        Reasons: {{ log.rejection_reasons.join(' | ') }}
                      </div>
                      @if (log.raw_response_snippet) {
                        <pre class="mt-1 p-2 rounded bg-slate-950 text-[11px] font-mono text-slate-400 whitespace-pre-wrap border border-slate-800">{{ log.raw_response_snippet }}</pre>
                      }
                    </div>
                  }
                </div>
              }
            </div>
          </div>
        } @else {
          <div class="flex-1 flex items-center justify-center p-8 text-center text-slate-400 text-sm">
            No items in the selected caption filter. Switch tabs above or keep items in Step 2.
          </div>
        }
      </div>
    </div>
  `,
})
export class Step4CaptionComponent implements OnInit {
  readonly api = inject(StudioApiService);
  readonly stepNavigate = output<StudioStep>();

  readonly captionFilter = signal<'all_kept' | 'captioned' | 'uncaptioned' | 'needs_manual'>('all_kept');
  readonly selectedIndex = signal<number>(0);

  readonly selectedSkillId = signal<string>('krea2_concept_v1');
  readonly classNoun = signal<string>('woman');
  readonly onlyUncaptioned = signal<boolean>(true);
  readonly isCaptioningJobRunning = signal<boolean>(false);

  // Split-View Image Zoom/Pan Signals
  readonly zoomLevel = signal<number>(1.0);
  readonly panX = signal<number>(0);
  readonly panY = signal<number>(0);
  private isPanning = false;
  private panStartX = 0;
  private panStartY = 0;

  // Live Editor & Diff Signals
  readonly draftCaption = signal<string>('');
  readonly baselineCaption = signal<string>('');
  readonly showDiffView = signal<boolean>(false);
  readonly enforceTriggerOnSave = signal<boolean>(true);

  readonly allKeptItems = computed<StudioMediaItem[]>(() => {
    const items = this.api.mediaItems();
    const kept = items.filter((i) => i.curation_state === 'keep' || i.curation_state === 'star');
    return kept.length > 0 ? kept : items;
  });

  readonly captionedCount = computed(
    () =>
      this.allKeptItems().filter(
        (i) =>
          i.caption_status === 'vlm_accepted' ||
          i.caption_status === 'manual' ||
          i.caption_status === 'approved',
      ).length,
  );

  readonly uncaptionedCount = computed(
    () => this.allKeptItems().filter((i) => i.caption_status === 'uncaptioned').length,
  );

  readonly needsManualItems = computed(() =>
    this.api.mediaItems().filter((i) => i.caption_status === 'needs_manual_caption'),
  );

  readonly filteredItems = computed<StudioMediaItem[]>(() => {
    const mode = this.captionFilter();
    if (mode === 'needs_manual') {
      return this.needsManualItems();
    }
    const base = this.allKeptItems();
    if (mode === 'captioned') {
      return base.filter(
        (i) =>
          i.caption_status === 'vlm_accepted' ||
          i.caption_status === 'manual' ||
          i.caption_status === 'approved',
      );
    }
    if (mode === 'uncaptioned') {
      return base.filter((i) => i.caption_status === 'uncaptioned');
    }
    return base;
  });

  readonly activeItem = computed<StudioMediaItem | null>(() => {
    const list = this.filteredItems();
    if (list.length === 0) return null;
    const clamped = Math.max(0, Math.min(this.selectedIndex(), list.length - 1));
    return list[clamped] ?? null;
  });

  readonly activeSkill = computed<StudioSkill | null>(() => {
    const sid = this.selectedSkillId();
    return this.api.skills().find((s) => s.id === sid || s.slug === sid) ?? this.api.skills()[0] ?? null;
  });

  readonly activeTriggerWord = computed<string>(() => {
    return this.api.activeDataset()?.trigger_word || this.api.settings().trigger_word || 'ohwx_subject';
  });

  readonly activeTriggerPosition = computed<TriggerPosition>(() => {
    return this.api.activeDataset()?.trigger_position || this.api.settings().trigger_position || 'prefix';
  });

  readonly modelOriginLabel = computed<string>(() => {
    const it = this.activeItem();
    if (!it) return 'No Item';
    if (it.caption_status === 'needs_manual_caption') {
      return '🚨 All 3 Cascade Layers Rejected — Needs Manual Caption';
    }
    const layer = it.active_caption_layer;
    const model = it.active_caption_model || 'VLM';
    if (layer === 'primary') {
      return `Captioned by Primary (${model})`;
    }
    if (layer === 'fallback_1') {
      return `Fallback 1 Triggered (${model})`;
    }
    if (layer === 'fallback_2') {
      return `Fallback 2 Triggered (${model})`;
    }
    if (layer === 'manual') {
      return 'Manual Override (Human Approved)';
    }
    return 'Uncaptioned';
  });

  readonly draftWordCount = computed<number>(() => {
    const text = this.draftCaption().trim();
    if (!text) return 0;
    return text.split(/\s+/).filter(Boolean).length;
  });

  readonly draftTokenCount = computed<number>(() => {
    const words = this.draftWordCount();
    return Math.round(words * 1.18);
  });

  readonly isWordCountInBudget = computed<boolean>(() => {
    const w = this.draftWordCount();
    const sk = this.activeSkill();
    const minW = sk?.min_words ?? 75;
    const maxW = sk?.max_words ?? 180;
    return w >= minW && w <= maxW;
  });

  readonly budgetProgressPercent = computed<number>(() => {
    const w = this.draftWordCount();
    const maxW = this.activeSkill()?.max_words ?? 180;
    return Math.min(100, Math.round((w / Math.max(1, maxW)) * 100));
  });

  readonly hasTriggerWordInDraft = computed<boolean>(() => {
    const trig = this.activeTriggerWord().trim().toLowerCase();
    if (!trig) return true;
    return this.draftCaption().toLowerCase().includes(trig);
  });

  readonly highlightedSegments = computed<CaptionHighlightSegment[]>(() => {
    const text = this.draftCaption() || '';
    const trig = this.activeTriggerWord().trim();
    if (!text) {
      return [{ text: '(Empty caption)', isTrigger: false }];
    }
    if (!trig) {
      return [{ text, isTrigger: false }];
    }
    const escaped = trig.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escaped})`, 'gi');
    const parts = text.split(regex);
    return parts
      .filter((p) => p.length > 0)
      .map((part) => ({
        text: part,
        isTrigger: part.toLowerCase() === trig.toLowerCase(),
      }));
  });

  readonly diffTokens = computed<DiffToken[]>(() => {
    return computeCaptionWordDiff(this.baselineCaption(), this.draftCaption());
  });

  readonly activeItemRejections = computed<RejectionLogEntry[]>(() => {
    const it = this.activeItem();
    if (!it) return [];
    return this.api.rejectionLogs().filter((r) => r.media_id === it.id);
  });

  constructor() {
    effect(() => {
      const it = this.activeItem();
      if (it) {
        const cap = it.active_caption ?? '';
        this.draftCaption.set(cap);
        this.baselineCaption.set(cap);
        this.resetZoomPan();
      }
    });
  }

  async ngOnInit(): Promise<void> {
    await this.api.loadSkills();
    const ds = this.api.activeDataset();
    if (ds) {
      if (ds.active_skill_id) {
        this.selectedSkillId.set(ds.active_skill_id);
      }
      if (this.api.mediaItems().length === 0) {
        await this.api.loadMediaItems(ds.id);
      }
      await this.api.loadRejections(ds.id);
    }
  }

  selectItemIndex(idx: number): void {
    this.selectedIndex.set(idx);
  }

  adjustZoom(delta: number): void {
    const next = Math.max(1.0, Math.min(4.0, this.zoomLevel() + delta));
    this.zoomLevel.set(next);
    if (next === 1.0) {
      this.panX.set(0);
      this.panY.set(0);
    }
  }

  resetZoomPan(): void {
    this.zoomLevel.set(1.0);
    this.panX.set(0);
    this.panY.set(0);
  }

  startPan(ev: MouseEvent): void {
    if (this.zoomLevel() <= 1.0) return;
    this.isPanning = true;
    this.panStartX = ev.clientX - this.panX();
    this.panStartY = ev.clientY - this.panY();
  }

  movePan(ev: MouseEvent): void {
    if (!this.isPanning) return;
    this.panX.set(ev.clientX - this.panStartX);
    this.panY.set(ev.clientY - this.panStartY);
  }

  endPan(): void {
    this.isPanning = false;
  }

  revertDraft(): void {
    this.draftCaption.set(this.baselineCaption());
  }

  async launchCascadeCaptionJob(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) return;
    this.isCaptioningJobRunning.set(true);
    try {
      await this.api.runDatasetCaptioning(ds.id, {
        skill_id: this.selectedSkillId(),
        trigger_word: this.activeTriggerWord(),
        trigger_position: this.activeTriggerPosition(),
        class_noun: this.classNoun().trim() || 'woman',
        only_uncaptioned: this.onlyUncaptioned(),
      });
    } finally {
      this.isCaptioningJobRunning.set(false);
    }
  }

  async saveAndApproveCaption(advanceNext = true): Promise<void> {
    const it = this.activeItem();
    if (!it) return;
    const text = this.draftCaption().trim();
    if (!text) {
      this.api.showToast('Caption text cannot be empty', 'warning');
      return;
    }
    const res = await this.api.saveManualCaption(it.id, {
      caption_text: text,
      enforce_trigger: this.enforceTriggerOnSave(),
      trigger_word: this.activeTriggerWord(),
      trigger_position: this.activeTriggerPosition(),
    });
    this.baselineCaption.set(res.caption_text);
    this.draftCaption.set(res.caption_text);

    if (advanceNext) {
      const list = this.filteredItems();
      if (this.selectedIndex() + 1 < list.length) {
        this.selectedIndex.update((i) => i + 1);
      }
    }
  }
}
