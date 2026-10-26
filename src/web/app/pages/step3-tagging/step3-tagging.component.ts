import {
  ChangeDetectionStrategy,
  Component,
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
  StudioStep,
  TagCategory,
} from '../../models/studio.models';
import { StudioApiService } from '../../services/studio-api.service';

interface HistogramBucketBar {
  key: string;
  label: string;
  count: number;
  percent: number;
}

@Component({
  selector: 'app-step3-tagging',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div data-testid="view-tagging" class="flex flex-col lg:flex-row h-full w-full gap-4 p-4 overflow-y-auto bg-slate-950 text-slate-100">
      <!-- Left Column: Rule-Based CV Tagger, Interactive Quick-Tagger & Auto-Stratifier Controls -->
      <div class="flex flex-col w-full lg:w-5/12 gap-4">
        <!-- Rule-Based CV Tagger Card (BaseTaggerPlugin) -->
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-bold uppercase tracking-wider text-orange-300">
                3A. Pluggable Heuristic & ML Auto-Tagger (BaseTaggerPlugin)
              </h2>
              <p class="text-xs text-slate-300 mt-0.5">
                Default non-ML CV heuristics classify framing, chiaroscuro lighting, color cast, and depth-of-field
              </p>
            </div>
            <span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/30 font-mono text-xs">
              Zero-ONNX Default
            </span>
          </div>

          <div class="grid grid-cols-2 gap-3 bg-slate-950 p-3 rounded-lg border border-slate-800">
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-300 font-semibold" for="plugin-select">Active Tagger Plugin</label>
              <select
                id="plugin-select"
                [ngModel]="selectedPluginId()"
                (ngModelChange)="selectedPluginId.set($event)"
                class="min-h-8 px-2.5 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs text-white font-mono"
              >
                <option value="rule_cv_heuristics">rule_cv_heuristics (OpenCV/NumPy)</option>
                <option value="wd14_swinv2_onnx" disabled>wd14_swinv2_onnx (Optional Plugin)</option>
                <option value="siglip_zero_shot" disabled>siglip_zero_shot (Optional Plugin)</option>
              </select>
            </div>

            <div class="flex flex-col gap-1">
              <div class="flex justify-between text-xs">
                <label for="min-confidence-range" class="text-slate-300 font-semibold">Min Confidence</label>
                <span class="font-mono text-sky-300">{{ minConfidence().toFixed(2) }}</span>
              </div>
              <input
                id="min-confidence-range"
                type="range"
                aria-label="Minimum tagger confidence"
                min="0.3"
                max="0.95"
                step="0.05"
                [ngModel]="minConfidence()"
                (ngModelChange)="minConfidence.set(+$event)"
                class="accent-sky-500 cursor-pointer mt-1"
              />
            </div>
          </div>

          <button
            type="button"
            [disabled]="isRunningAutotag()"
            (click)="runRuleBasedTagger()"
            class="w-full min-h-8 min-w-8 px-4 py-2.5 rounded-lg bg-sky-700 hover:bg-sky-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer disabled:opacity-50 transition"
          >
            @if (isRunningAutotag()) {
              Running Rule-Based CV Tagger (BaseTaggerPlugin)...
            } @else {
              ⚡ Run Rule-Based CV Tagger (BaseTaggerPlugin)
            }
          </button>
        </section>

        <!-- Interactive Category Quick-Tagger Card -->
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-bold uppercase tracking-wider text-slate-200">
              3B. Interactive Category Quick-Tagger (Kept Items)
            </h2>
            <span class="text-xs font-mono text-slate-300">
              Applies to {{ keptItemIds().length }} kept/starred items
            </span>
          </div>

          <!-- Category Selector Pills -->
          <div class="flex flex-wrap gap-1.5">
            @for (cat of tagCategories; track cat) {
              <button
                type="button"
                (click)="activeTagCategory.set(cat)"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded text-xs font-mono transition cursor-pointer"
                [class.bg-orange-700]="activeTagCategory() === cat"
                [class.text-white]="activeTagCategory() === cat"
                [class.bg-slate-800]="activeTagCategory() !== cat"
                [class.text-slate-200]="activeTagCategory() !== cat"
              >
                {{ cat }}
              </button>
            }
          </div>

          <!-- Preset Tag Pills for Selected Category -->
          <div class="flex flex-wrap gap-1.5 bg-slate-950 p-3 rounded-lg border border-slate-800">
            @for (preset of categoryPresets(); track preset) {
              <button
                type="button"
                (click)="applyTagToKeptItems(preset, activeTagCategory())"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded-full bg-slate-900 hover:bg-sky-700 text-slate-200 hover:text-white border border-slate-700 text-xs font-mono cursor-pointer transition"
              >
                + {{ preset }}
              </button>
            }
          </div>

          <!-- Custom Tag Input -->
          <div class="flex items-center gap-2">
            <input
              id="custom-tag-input"
              type="text"
              aria-label="Custom tag name"
              [ngModel]="customTagName()"
              (ngModelChange)="customTagName.set($event)"
              placeholder="Custom tag name (e.g., tailored_wool_coat)..."
              class="flex-1 min-h-8 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-white focus:border-orange-500 focus:outline-none"
            />
            <button
              type="button"
              [disabled]="!customTagName().trim()"
              (click)="addCustomTagToKept()"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white border border-slate-700 cursor-pointer disabled:opacity-50"
            >
              Add Tag
            </button>
          </div>
        </section>

        <!-- Auto-Balance / DatasetStratifier Card -->
        <section class="rounded-xl border border-emerald-500/30 bg-slate-900 p-4 shadow-lg flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-bold uppercase tracking-wider text-emerald-300">
                3C. Auto-Balance & MMR Dataset Stratifier
              </h2>
              <p class="text-xs text-slate-300">
                Balances Composite Quality Q against aspect ratio, brightness key, temporal decile, and pHash Hamming distance
              </p>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-4 bg-slate-950 p-3 rounded-lg border border-slate-800">
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-300 font-semibold" for="strat-target-input">Target Dataset Size (K)</label>
              <input
                id="strat-target-input"
                type="number"
                min="5"
                max="5000"
                [ngModel]="targetCount()"
                (ngModelChange)="targetCount.set(+$event)"
                class="min-h-8 px-2.5 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-white"
              />
            </div>

            <div class="flex flex-col gap-1">
              <div class="flex justify-between text-xs">
                <label for="diversity-weight-range" class="text-slate-300 font-semibold">Diversity Weight (λ)</label>
                <span class="font-mono text-emerald-300">{{ diversityWeight().toFixed(2) }}</span>
              </div>
              <input
                id="diversity-weight-range"
                type="range"
                aria-label="Stratifier diversity weight"
                min="0.0"
                max="1.0"
                step="0.05"
                [ngModel]="diversityWeight()"
                (ngModelChange)="diversityWeight.set(+$event)"
                class="accent-emerald-500 cursor-pointer mt-1"
              />
            </div>
          </div>

          <div class="flex items-center gap-2">
            <button
              type="button"
              [disabled]="isStratifying()"
              (click)="runAutoStratify()"
              class="flex-1 min-h-8 min-w-8 px-3 py-2.5 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer disabled:opacity-50 transition"
            >
              @if (isStratifying()) {
                Stratifying Selection...
              } @else {
                ⚖️ Auto-Balance / Stratify Selection (Target={{ targetCount() }})
              }
            </button>

            <button
              type="button"
              (click)="stepNavigate.emit('captioning')"
              class="min-h-8 min-w-8 px-4 py-2.5 rounded-lg bg-orange-700 hover:bg-orange-600 text-white font-bold text-xs cursor-pointer"
            >
              Step 4: Captioning →
            </button>
          </div>
        </section>
      </div>

      <!-- Right Column: Visual Distribution Histograms & Outlier Warnings -->
      <div class="flex flex-col w-full lg:w-7/12 gap-4">
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg flex flex-col gap-5">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-base font-bold text-white">
                Dataset Distribution Histograms & Outlier Telemetry
              </h2>
              <p class="text-xs text-slate-300">
                Real-time stratification audit across {{ api.stratification()?.total_items ?? 0 }} non-duplicate items
              </p>
            </div>
            <button
              type="button"
              (click)="refreshStratification()"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 border border-slate-700 cursor-pointer"
            >
              Recompute Histograms
            </button>
          </div>

          <!-- Outlier Warning Banner & Actionable Recommendations (Fixed min-h to prevent CLS) -->
          <div class="min-h-24 rounded-xl border border-amber-500/40 bg-slate-950 p-3.5 flex flex-col justify-center gap-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold uppercase tracking-wider text-amber-300">
                ⚠️ Stratifier Outlier & Coverage Diagnostics
              </span>
              <span class="px-2 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-500/40 font-mono text-xs font-bold">
                {{ api.stratification()?.outlier_ids?.length ?? 0 }} Quality/Gate Outliers Detected
              </span>
            </div>

            @if ((api.stratification()?.recommendations?.length ?? 0) > 0) {
              @for (rec of api.stratification()?.recommendations ?? []; track rec) {
                <div class="text-xs text-amber-200 flex items-start gap-2">
                  <span class="text-amber-300">•</span>
                  <span>{{ rec }}</span>
                </div>
              }
            } @else {
              <div class="text-xs text-slate-300">
                Stratification buckets balanced across current selection.
              </div>
            }
          </div>

          <!-- Histogram Grid: Aspect Ratio, Lighting Key, Color Temperature -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <!-- Aspect Ratio Histogram -->
            <div class="rounded-lg border border-slate-800 bg-slate-950 p-3.5 flex flex-col gap-2.5">
              <div class="text-xs font-bold uppercase tracking-wider text-sky-300">
                Aspect Ratio Buckets
              </div>
              @for (b of aspectBuckets(); track b.key) {
                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs font-mono">
                    <span class="text-slate-200">{{ b.label }}</span>
                    <span class="text-sky-300">{{ b.count }} ({{ b.percent }}%)</span>
                  </div>
                  <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                    <div class="h-full bg-sky-500 transition-all" [style.width.%]="b.percent"></div>
                  </div>
                </div>
              }
            </div>

            <!-- Lighting & Luminance Key Histogram -->
            <div class="rounded-lg border border-slate-800 bg-slate-950 p-3.5 flex flex-col gap-2.5">
              <div class="text-xs font-bold uppercase tracking-wider text-amber-300">
                Lighting / Brightness Key
              </div>
              @for (b of brightnessBuckets(); track b.key) {
                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs font-mono">
                    <span class="text-slate-200">{{ b.label }}</span>
                    <span class="text-amber-300">{{ b.count }} ({{ b.percent }}%)</span>
                  </div>
                  <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                    <div class="h-full bg-amber-500 transition-all" [style.width.%]="b.percent"></div>
                  </div>
                </div>
              }
            </div>

            <!-- Color Temperature Histogram -->
            <div class="rounded-lg border border-slate-800 bg-slate-950 p-3.5 flex flex-col gap-2.5">
              <div class="text-xs font-bold uppercase tracking-wider text-purple-300">
                Color Temperature & Cast
              </div>
              @for (b of colorTempBuckets(); track b.key) {
                <div class="flex flex-col gap-1">
                  <div class="flex justify-between text-xs font-mono">
                    <span class="text-slate-200">{{ b.label }}</span>
                    <span class="text-purple-300">{{ b.count }} ({{ b.percent }}%)</span>
                  </div>
                  <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                    <div class="h-full bg-purple-500 transition-all" [style.width.%]="b.percent"></div>
                  </div>
                </div>
              }
            </div>
          </div>

          <!-- Temporal Decile Spread (10 Timeline Bins) -->
          <div class="rounded-lg border border-slate-800 bg-slate-950 p-4 flex flex-col gap-3">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold uppercase tracking-wider text-emerald-300">
                Temporal Timeline Decile Distribution (0% → 100%)
              </span>
              <span class="text-xs font-mono text-slate-300">
                Mean Q: {{ api.stratification()?.quality_mean ?? 0 }} ± {{ api.stratification()?.quality_std ?? 0 }}
              </span>
            </div>

            <div class="grid grid-cols-10 gap-2 items-end h-28 pt-2 px-1">
              @for (d of temporalDecileBars(); track d.index) {
                <div class="flex flex-col items-center gap-1 h-full justify-end">
                  <span class="text-xs font-mono text-slate-200">{{ d.count }}</span>
                  <div class="w-full bg-slate-800 rounded-t overflow-hidden flex items-end h-20">
                    <div
                      class="w-full bg-emerald-500 rounded-t transition-all"
                      [style.height.%]="d.heightPercent"
                    ></div>
                  </div>
                  <span class="text-xs font-mono text-slate-300">{{ d.label }}</span>
                </div>
              }
            </div>
          </div>

          <!-- Tag Category Coverage Matrix -->
          <div class="rounded-lg border border-slate-800 bg-slate-950 p-4 flex flex-col gap-3 min-h-32">
            <div class="text-xs font-bold uppercase tracking-wider text-slate-200">
              Confirmed Tag Category Coverage
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-3 gap-2.5 min-h-20">
              @for (entry of tagCoverageEntries(); track entry.category) {
                <div class="p-2.5 rounded border border-slate-800 bg-slate-900 flex flex-col gap-1">
                  <div class="flex items-center justify-between text-xs">
                    <span class="font-bold text-orange-300 uppercase">{{ entry.category }}</span>
                    <span class="font-mono text-slate-300">{{ entry.total }} tags</span>
                  </div>
                  <div class="flex flex-wrap gap-1 mt-1">
                    @for (t of entry.topTags; track t.slug) {
                      <span class="px-1.5 py-0.5 rounded bg-slate-950 text-slate-200 border border-slate-800 text-xs font-mono">
                        {{ t.slug }} ({{ t.count }})
                      </span>
                    }
                  </div>
                </div>
              }
              @if (tagCoverageEntries().length === 0) {
                <div class="col-span-3 text-xs text-slate-300 py-3 text-center">
                  No tags assigned yet. Click "Run Rule-Based CV Tagger" on the left to populate heuristic tags automatically.
                </div>
              }
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
})
export class Step3TaggingComponent implements OnInit {
  readonly api = inject(StudioApiService);
  readonly stepNavigate = output<StudioStep>();

  readonly selectedPluginId = signal<string>('rule_cv_heuristics');
  readonly minConfidence = signal<number>(0.6);
  readonly isRunningAutotag = signal<boolean>(false);

  readonly activeTagCategory = signal<TagCategory>('lighting');
  readonly customTagName = signal<string>('');

  readonly targetCount = signal<number>(150);
  readonly diversityWeight = signal<number>(0.5);
  readonly isStratifying = signal<boolean>(false);

  constructor() {
    effect(() => {
      const ds = this.api.activeDataset();
      if (!ds) return;
      untracked(() => {
        this.targetCount.set(ds.target_count || 150);
        if (!this.api.stratification()) {
          void this.api.loadStratification(ds.id);
        }
        if (this.api.mediaItems().length === 0 && !this.api.loadingItems()) {
          void this.api.loadMediaItems(ds.id, { include_duplicates: true });
        }
      });
    });
  }

  readonly tagCategories: TagCategory[] = [
    'lighting',
    'shot_scale',
    'pose',
    'outfit',
    'expression',
    'style_element',
    'environment',
    'equipment',
  ];

  private readonly presetsByCategory: Record<TagCategory, string[]> = {
    lighting: [
      'chiaroscuro_high_contrast',
      'low_key_dark',
      'balanced_midtone',
      'high_key_bright',
      'rim_lighting',
      'window_daylight',
    ],
    shot_scale: [
      'close_up',
      'medium_shot',
      'three_quarter_shot',
      'full_body',
      'portrait_tall',
      'landscape_wide',
    ],
    pose: [
      'standing_poised',
      'seated_relaxed',
      'profile_turn',
      'over_shoulder',
      'leaning_wall',
      'hands_clasped',
    ],
    outfit: [
      'tailored_suit',
      'evening_gown',
      'structured_leather',
      'silk_blouse',
      'minimalist_casual',
    ],
    expression: [
      'direct_gaze',
      'composed_neutral',
      'subtle_smile',
      'contemplative_downcast',
      'intense_focus',
    ],
    style_element: [
      'close_focus_bokeh',
      'full_scene_deep_dof',
      'warm_tungsten_cast',
      'cool_daylight_cast',
      'monochrome_desaturated',
      'vivid_saturated',
    ],
    environment: [
      'studio_backdrop',
      'architectural_interior',
      'industrial_loft',
      'natural_exterior',
    ],
    equipment: [
      '85mm_prime_lens',
      '35mm_wide_angle',
      'metallic_hardware',
      'tactile_restraints',
    ],
  };

  readonly categoryPresets = computed<string[]>(() => {
    const cat = this.activeTagCategory();
    return this.presetsByCategory[cat] ?? [];
  });

  readonly keptItemIds = computed<number[]>(() => {
    return this.api
      .mediaItems()
      .filter((i) => i.curation_state === 'keep' || i.curation_state === 'star')
      .map((i) => i.id);
  });

  readonly aspectBuckets = computed<HistogramBucketBar[]>(() => {
    const strat = this.api.stratification();
    const total = Math.max(1, strat?.total_items ?? 1);
    const map = strat?.aspect_ratio_buckets ?? {};
    return [
      { key: 'portrait_tall', label: 'Portrait (<0.8)', count: map['portrait_tall'] ?? 0 },
      { key: 'square', label: 'Square (0.8..1.2)', count: map['square'] ?? 0 },
      { key: 'landscape_wide', label: 'Landscape (>1.2)', count: map['landscape_wide'] ?? 0 },
    ].map((b) => ({
      ...b,
      percent: Math.round((b.count / total) * 100),
    }));
  });

  readonly brightnessBuckets = computed<HistogramBucketBar[]>(() => {
    const strat = this.api.stratification();
    const total = Math.max(1, strat?.total_items ?? 1);
    const map = strat?.brightness_buckets ?? {};
    return [
      { key: 'chiaroscuro', label: 'Chiaroscuro Contrast', count: map['chiaroscuro'] ?? 0 },
      { key: 'low_key', label: 'Low-Key Dark', count: map['low_key'] ?? 0 },
      { key: 'balanced_midtone', label: 'Balanced Midtone', count: map['balanced_midtone'] ?? 0 },
      { key: 'high_key', label: 'High-Key Bright', count: map['high_key'] ?? 0 },
    ].map((b) => ({
      ...b,
      percent: Math.round((b.count / total) * 100),
    }));
  });

  readonly colorTempBuckets = computed<HistogramBucketBar[]>(() => {
    const strat = this.api.stratification();
    const total = Math.max(1, strat?.total_items ?? 1);
    const map = strat?.color_temp_buckets ?? {};
    return [
      { key: 'warm', label: 'Warm Tungsten', count: map['warm'] ?? 0 },
      { key: 'neutral', label: 'Neutral Balance', count: map['neutral'] ?? 0 },
      { key: 'cool', label: 'Cool Daylight', count: map['cool'] ?? 0 },
      { key: 'monochrome', label: 'Monochrome / Low-Sat', count: map['monochrome'] ?? 0 },
    ].map((b) => ({
      ...b,
      percent: Math.round((b.count / total) * 100),
    }));
  });

  readonly temporalDecileBars = computed(() => {
    const deciles = this.api.stratification()?.temporal_deciles ?? [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    const maxVal = Math.max(1, ...deciles);
    return deciles.map((c, idx) => ({
      index: idx,
      label: `${idx * 10}%`,
      count: c,
      heightPercent: Math.max(4, Math.round((c / maxVal) * 100)),
    }));
  });

  readonly tagCoverageEntries = computed(() => {
    const cov = this.api.stratification()?.tag_category_coverage ?? {};
    return Object.entries(cov).map(([category, info]) => ({
      category,
      total: info.total_assignments,
      topTags: Object.entries(info.top_tags ?? {})
        .slice(0, 5)
        .map(([slug, count]) => ({ slug, count })),
    }));
  });

  async ngOnInit(): Promise<void> {
    const ds = this.api.activeDataset();
    if (ds) {
      this.targetCount.set(ds.target_count || 150);
      await this.refreshStratification();
    }
  }

  async refreshStratification(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) return;
    await this.api.loadStratification(ds.id);
  }

  async runRuleBasedTagger(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) return;
    this.isRunningAutotag.set(true);
    try {
      await this.api.runAutotag(ds.id, this.selectedPluginId(), this.minConfidence());
    } finally {
      this.isRunningAutotag.set(false);
    }
  }

  async applyTagToKeptItems(tagName: string, category: TagCategory): Promise<void> {
    const ds = this.api.activeDataset();
    const ids = this.keptItemIds();
    if (!ds || ids.length === 0) {
      this.api.showToast('No kept items to tag. Keep items in Step 2 first.', 'warning');
      return;
    }
    await this.api.batchTagItems(ds.id, {
      item_ids: ids,
      add_tags: [{ name: tagName, category }],
    });
    await this.refreshStratification();
    this.api.showToast(`Applied tag "${tagName}" (${category}) to ${ids.length} kept items`, 'success');
  }

  async addCustomTagToKept(): Promise<void> {
    const name = this.customTagName().trim();
    if (!name) return;
    await this.applyTagToKeptItems(name, this.activeTagCategory());
    this.customTagName.set('');
  }

  async runAutoStratify(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) return;
    this.isStratifying.set(true);
    try {
      await this.api.applyStratification(ds.id, this.targetCount(), this.diversityWeight());
    } finally {
      this.isStratifying.set(false);
    }
  }
}
