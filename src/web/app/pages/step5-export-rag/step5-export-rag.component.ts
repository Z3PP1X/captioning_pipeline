import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  RagGenerateResponse,
  RagIndexResponse,
  RagRetrievedExemplar,
  TriggerPosition,
} from '../../models/studio.models';
import { StudioApiService } from '../../services/studio-api.service';

@Component({
  selector: 'app-step5-export-rag',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div data-testid="view-export_rag" class="flex flex-col xl:flex-row h-full w-full gap-4 p-4 overflow-y-auto bg-slate-950 text-slate-100">
      <!-- Left Column (40%): Standardized LoRA Dataset Exporter & dataset_card.json Inspector -->
      <div class="flex flex-col w-full xl:w-5/12 gap-4">
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg flex flex-col gap-4">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-base font-bold text-white">
                5A. Standardized LoRA Dataset Exporter
              </h2>
              <p class="text-xs text-slate-300">
                Lanczos-4 max-edge clamping, multiple-of-16 DiT latent grid snapping, paired .txt captions, metadata.jsonl, dataset_card.json & .zip archive
              </p>
            </div>
            <span class="px-2.5 py-1 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/40 font-mono text-xs font-bold">
              Kohya / AI-Toolkit Ready
            </span>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <!-- Image Format -->
            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="export-format-select">Image Format</label>
              <select
                id="export-format-select"
                [ngModel]="imageFormat()"
                (ngModelChange)="imageFormat.set($event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-white"
              >
                <option value="png">Lossless 24-bit sRGB (.png)</option>
                <option value="jpg">High-Purity JPEG Q95 (.jpg)</option>
              </select>
            </div>

            <!-- Max Edge Clamp -->
            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="export-edge-select">Max Edge Resolution</label>
              <select
                id="export-edge-select"
                [ngModel]="maxEdge()"
                (ngModelChange)="maxEdge.set(+$event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-white"
              >
                <option [ngValue]="1024">1024 px (Standard Flux / SDXL)</option>
                <option [ngValue]="1536">1536 px (High-Res Krea 2)</option>
                <option [ngValue]="2048">2048 px (Ultra-Res Master)</option>
              </select>
            </div>

            <!-- Override Triggerword -->
            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="export-trigger-input">Verified Triggerword</label>
              <input
                id="export-trigger-input"
                type="text"
                [ngModel]="exportTriggerWord()"
                (ngModelChange)="exportTriggerWord.set($event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-sky-500/50 text-xs font-mono text-sky-300"
              />
            </div>

            <!-- Trigger Position -->
            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="export-pos-select">Trigger Placement</label>
              <select
                id="export-pos-select"
                [ngModel]="exportTriggerPos()"
                (ngModelChange)="exportTriggerPos.set($event)"
                class="min-h-8 px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-white"
              >
                <option value="prefix">prefix (Token 0)</option>
                <option value="suffix">suffix (Terminal)</option>
                <option value="natural_integration">natural_integration</option>
              </select>
            </div>
          </div>

          <div class="flex flex-col gap-2 bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
            <label class="flex items-center gap-2 min-h-8 text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                aria-label="Snap dimensions to multiples of 16"
                [ngModel]="snapMultiple16()"
                (ngModelChange)="snapMultiple16.set($event)"
                class="accent-emerald-500"
              />
              <span>Snap (W, H) to Multiples of 16 (Flux / Krea 2 / Wan DiT Latent Grid)</span>
            </label>

            <label class="flex items-center gap-2 min-h-8 text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                aria-label="Generate downloadable zip archive"
                [ngModel]="createZipArchive()"
                (ngModelChange)="createZipArchive.set($event)"
                class="accent-emerald-500"
              />
              <span>Generate downloadable .zip archive in /data/exports</span>
            </label>
          </div>

          <div class="flex items-center gap-3">
            <button
              type="button"
              [disabled]="isExporting()"
              (click)="runExport()"
              class="flex-1 min-h-8 min-w-8 px-4 py-2.5 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer disabled:opacity-50 transition"
            >
              @if (isExporting()) {
                Exporting Normalized Dataset...
              } @else {
                📦 Export Dataset + metadata.jsonl + .zip
              }
            </button>

            @if (api.activeDataset(); as ds) {
              <a
                [href]="api.getExportDownloadUrl(ds.id)"
                target="_blank"
                rel="noopener"
                title="Download exported dataset archive (.zip)"
                class="min-h-8 min-w-8 px-4 py-2.5 rounded-lg bg-sky-700 hover:bg-sky-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer flex items-center justify-center"
              >
                ⬇ Download .zip
              </a>
            }
          </div>

          <!-- dataset_card.json Summary Viewer -->
          @if (api.lastExportResult(); as exp) {
            <div class="mt-2 rounded-xl border border-emerald-500/40 bg-slate-950 p-4 flex flex-col gap-2 text-xs font-mono">
              <div class="flex items-center justify-between text-emerald-300 font-bold">
                <span>✓ Export Complete ({{ exp.exported_count }} items)</span>
                <span>{{ exp.dataset_card.image_format.toUpperCase() }} @ {{ exp.dataset_card.max_edge }}px</span>
              </div>
              <div class="text-slate-200 truncate" [title]="exp.export_dir">Dir: {{ exp.export_dir }}</div>
              <div class="text-slate-300 truncate" [title]="exp.metadata_jsonl_path">Manifest: {{ exp.metadata_jsonl_path }}</div>
              <details class="mt-1">
                <summary class="cursor-pointer text-sky-300 hover:underline">
                  Inspect dataset_card.json Audit Report
                </summary>
                <pre class="mt-2 p-2.5 rounded bg-slate-950 border border-slate-800 text-xs text-slate-200 overflow-x-auto max-h-60">{{ formatJson(exp.dataset_card) }}</pre>
              </details>
            </div>
          }
        </section>
      </div>

      <!-- Right Column (60%): Local Non-ML "LoRA Prompt Studio" (RAG UI) -->
      <div class="flex flex-col w-full xl:w-7/12 gap-4">
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg flex flex-col gap-4">
          <!-- Header & Index Builder Bar -->
          <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-base font-bold text-white flex items-center gap-2">
                <span>5B. Local "LoRA Prompt Studio" (Hybrid FTS5 + SVD-128 RAG)</span>
              </h2>
              <p class="text-xs text-slate-300">
                Ground Krea 2 inference prompts in your real dataset captions via Reciprocal Rank Fusion (RRF) & MMR diversification
              </p>
            </div>

            <div class="flex items-center gap-2">
              <select
                [ngModel]="denseBackend()"
                (ngModelChange)="denseBackend.set($event)"
                aria-label="Dense RAG Vector Backend"
                class="min-h-8 px-2.5 py-1.5 rounded bg-slate-950 border border-slate-700 text-xs font-mono text-slate-200"
              >
                <option value="tfidf_svd_128">Tier 2A: Local TF-IDF + SVD 128-D (Zero-ML)</option>
                <option value="openrouter_embedding">Tier 2B: OpenRouter Embeddings API</option>
              </select>

              <button
                type="button"
                [disabled]="isIndexing()"
                (click)="rebuildRagIndex()"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-bold cursor-pointer disabled:opacity-50"
              >
                @if (isIndexing()) {
                  Indexing...
                } @else {
                  ⚡ Build / Refresh RAG Index
                }
              </button>
            </div>
          </div>

          @if (indexStats(); as idxStat) {
            <div class="px-3 py-1.5 rounded-lg bg-indigo-950 border border-indigo-500/30 flex items-center justify-between text-xs font-mono text-indigo-200">
              <span>FTS5 BM25 Ready: {{ idxStat.fts5_ready ? 'YES' : 'NO' }}</span>
              <span>Indexed Captions: {{ idxStat.indexed_documents }}</span>
              <span>Vector Backend: {{ idxStat.dense_backend }} ({{ idxStat.duration_s.toFixed(3) }}s)</span>
            </div>
          }

          <!-- Multi-LoRA Triggerword & Retrieval Configuration -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="rag-char-trigger">
                Primary Character / Concept Trigger
              </label>
              <input
                id="rag-char-trigger"
                type="text"
                [ngModel]="primaryTrigger()"
                (ngModelChange)="primaryTrigger.set($event)"
                placeholder="e.g., ohwx_woman"
                class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-950 border border-sky-500/50 text-xs font-mono text-sky-300"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="rag-style-trigger">
                Compatible Style / Secondary Trigger (Optional)
              </label>
              <input
                id="rag-style-trigger"
                type="text"
                [ngModel]="secondaryStyleTrigger()"
                (ngModelChange)="secondaryStyleTrigger.set($event)"
                placeholder="e.g., s3cr3t_noir_style"
                class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-950 border border-purple-500/50 text-xs font-mono text-purple-300"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="text-xs font-semibold text-slate-300" for="rag-mode-select">Retrieval Fusion Mode</label>
              <select
                id="rag-mode-select"
                [ngModel]="retrievalMode()"
                (ngModelChange)="retrievalMode.set($event)"
                class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono text-white"
              >
                <option value="hybrid">Hybrid (FTS5 BM25 + SVD-128 Cosine RRF)</option>
                <option value="fts5_only">FTS5 Lexical BM25 Only</option>
                <option value="dense_only">Dense Vector Cosine Only</option>
              </select>
            </div>
          </div>

          <!-- Tag Category Filter Pills for Retrieval Grounding -->
          <div class="flex flex-wrap items-center gap-1.5 bg-slate-950 p-2.5 rounded-lg border border-slate-800">
            <span class="text-xs font-bold uppercase text-slate-300 mr-1">Filter by Tag:</span>
            @for (tagPill of ragQuickFilterTags; track tagPill.slug) {
              <button
                type="button"
                (click)="toggleRagTagFilter(tagPill.category, tagPill.slug)"
                [title]="'Filter RAG retrieval by ' + tagPill.slug"
                class="min-h-8 min-w-8 px-3 py-1.5 rounded-full text-xs font-mono border cursor-pointer transition"
                [class.bg-sky-700]="isRagTagSelected(tagPill.category, tagPill.slug)"
                [class.text-white]="isRagTagSelected(tagPill.category, tagPill.slug)"
                [class.border-sky-400]="isRagTagSelected(tagPill.category, tagPill.slug)"
                [class.bg-slate-900]="!isRagTagSelected(tagPill.category, tagPill.slug)"
                [class.text-slate-200]="!isRagTagSelected(tagPill.category, tagPill.slug)"
                [class.border-slate-700]="!isRagTagSelected(tagPill.category, tagPill.slug)"
              >
                {{ tagPill.slug }}
              </button>
            }
          </div>

          <!-- Natural Language Scene Description Input -->
          <div class="flex flex-col gap-1.5">
            <label class="text-xs font-semibold text-slate-300" for="rag-query-input">
              Scene Description / Prompt Concept Query
            </label>
            <div class="flex gap-2">
              <input
                id="rag-query-input"
                type="text"
                [ngModel]="sceneQuery()"
                (ngModelChange)="sceneQuery.set($event)"
                (keydown.enter)="synthesizeGroundedPrompts()"
                placeholder="Describe desired scene (e.g., dramatic chiaroscuro portrait in tailored coat near rain window)..."
                class="flex-1 min-h-8 px-3.5 py-2 rounded-lg bg-slate-950 border border-slate-700 text-sm text-white focus:border-orange-500 focus:outline-none"
              />
              <button
                type="button"
                [disabled]="isGenerating()"
                (click)="retrieveOnly()"
                class="min-h-8 min-w-8 px-3.5 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 border border-slate-700 cursor-pointer"
              >
                Retrieve Only
              </button>
              <button
                type="button"
                [disabled]="isGenerating()"
                (click)="synthesizeGroundedPrompts()"
                class="min-h-8 min-w-8 px-4 py-2 rounded-lg bg-orange-700 hover:bg-orange-600 text-xs font-bold uppercase tracking-wider text-white shadow cursor-pointer disabled:opacity-50"
              >
                @if (isGenerating()) {
                  Synthesizing...
                } @else {
                  ✨ Synthesize 3 Archetypes
                }
              </button>
            </div>
          </div>

          <!-- Synthesized 3 Krea 2 Prompt Archetype Cards (A: Tags, B: Short Anchor, C: Dense Narrative) -->
          @if (generatedResult(); as gen) {
            <div class="grid grid-cols-1 gap-3 pt-1">
              @for (arch of archetypeCards(); track arch.key) {
                <div class="rounded-xl border border-orange-500/40 bg-slate-950 p-3.5 flex flex-col gap-2">
                  <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                      <span class="px-2 py-0.5 rounded bg-orange-950 text-orange-300 border border-orange-500/40 text-xs font-bold">
                        {{ arch.title }}
                      </span>
                      <span class="text-xs font-mono text-sky-300">
                        ~{{ arch.card.token_count }} tokens
                      </span>
                    </div>
                    <button
                      type="button"
                      (click)="copyPromptToClipboard(arch.card.prompt)"
                      class="min-h-8 min-w-8 px-3 py-1.5 rounded bg-slate-800 hover:bg-emerald-700 text-slate-200 hover:text-white text-xs font-semibold cursor-pointer transition"
                    >
                      📋 Copy Prompt
                    </button>
                  </div>

                  <p class="text-xs leading-relaxed text-slate-100 font-mono bg-slate-900 p-3 rounded-lg border border-slate-800 select-all">
                    {{ arch.card.prompt }}
                  </p>

                  @if (arch.card.warnings && arch.card.warnings.length > 0) {
                    <div class="flex flex-wrap gap-1.5 text-xs font-mono text-emerald-300">
                      @for (w of arch.card.warnings; track w) {
                        <span class="px-2 py-0.5 rounded bg-emerald-950 border border-emerald-500/30">
                          Linter: {{ w }}
                        </span>
                      }
                    </div>
                  }
                </div>
              }
            </div>
          }

          <!-- Retrieved Grounding Reference Captions with BM25, Cosine cos(θ) & RRF Scores -->
          <div class="flex flex-col gap-2 pt-2 border-t border-slate-800">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold uppercase tracking-wider text-slate-200">
                Retrieved Dataset Grounding Exemplars ({{ retrievedExemplars().length }})
              </span>
              <span class="text-xs font-mono text-slate-300">
                Ranked via Reciprocal Rank Fusion (RRF k=60) + MMR (λ=0.7)
              </span>
            </div>

            @if (retrievedExemplars().length === 0) {
              <div class="p-4 text-center text-xs text-slate-300 border border-dashed border-slate-800 rounded-lg">
                Click "Build / Refresh RAG Index" and run a query above to inspect retrieved training captions and similarity scores.
              </div>
            } @else {
              <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                @for (ex of retrievedExemplars(); track ex.doc_id) {
                  <div class="p-3 rounded-lg border border-slate-800 bg-slate-950 flex flex-col gap-1.5 text-xs">
                    <div class="flex items-center justify-between font-mono text-xs">
                      <span class="font-bold text-orange-300">{{ ex.stem }}</span>
                      <div class="flex items-center gap-2">
                        <span class="px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-500/30">
                          RRF: {{ ex.score.toFixed(3) }}
                        </span>
                        <span class="text-emerald-300">BM25: {{ ex.bm25_score.toFixed(2) }}</span>
                        <span class="text-sky-300">cos(θ): {{ ex.cosine_score.toFixed(2) }}</span>
                      </div>
                    </div>
                    <p class="text-slate-200 text-xs leading-relaxed line-clamp-3" [title]="ex.full_caption">
                      {{ ex.full_caption }}
                    </p>
                  </div>
                }
              </div>
            }
          </div>
        </section>
      </div>
    </div>
  `,
})
export class Step5ExportRagComponent implements OnInit {
  readonly api = inject(StudioApiService);

  // Exporter Signals
  readonly imageFormat = signal<'png' | 'jpg'>('png');
  readonly maxEdge = signal<number>(2048);
  readonly snapMultiple16 = signal<boolean>(true);
  readonly createZipArchive = signal<boolean>(true);
  readonly exportTriggerWord = signal<string>('ohwx_subject');
  readonly exportTriggerPos = signal<TriggerPosition>('prefix');
  readonly isExporting = signal<boolean>(false);

  // RAG & Prompt Studio Signals
  readonly denseBackend = signal<'tfidf_svd_128' | 'openrouter_embedding'>('tfidf_svd_128');
  readonly retrievalMode = signal<'hybrid' | 'fts5_only' | 'dense_only'>('hybrid');
  readonly primaryTrigger = signal<string>('ohwx_subject');
  readonly secondaryStyleTrigger = signal<string>('');
  readonly sceneQuery = signal<string>(
    'dramatic chiaroscuro studio portrait in tailored coat with poised posture',
  );
  readonly selectedRagTags = signal<Record<string, string[]>>({});

  readonly isIndexing = signal<boolean>(false);
  readonly isGenerating = signal<boolean>(false);
  readonly indexStats = signal<RagIndexResponse | null>(null);
  readonly retrievedExemplars = signal<RagRetrievedExemplar[]>([]);
  readonly generatedResult = signal<RagGenerateResponse | null>(null);

  readonly ragQuickFilterTags = [
    { category: 'lighting', slug: 'chiaroscuro_high_contrast' },
    { category: 'lighting', slug: 'low_key_dark' },
    { category: 'lighting', slug: 'high_key_bright' },
    { category: 'shot_scale', slug: 'close_up' },
    { category: 'shot_scale', slug: 'full_body' },
    { category: 'shot_scale', slug: 'portrait_tall' },
    { category: 'style_element', slug: 'close_focus_bokeh' },
    { category: 'style_element', slug: 'warm_tungsten_cast' },
  ];

  readonly archetypeCards = computed(() => {
    const gen = this.generatedResult();
    if (!gen) return [];
    return [
      {
        key: 'a',
        title: 'Archetype A — Concise Tag Prompt (25–50 tokens)',
        card: gen.archetypes.a,
      },
      {
        key: 'b',
        title: 'Archetype B — Short Anchor Prose (50–120 tokens)',
        card: gen.archetypes.b,
      },
      {
        key: 'c',
        title: 'Archetype C — Dense Visual Narrative (180–280 tokens)',
        card: gen.archetypes.c,
      },
    ];
  });

  ngOnInit(): void {
    const ds = this.api.activeDataset();
    if (ds) {
      this.exportTriggerWord.set(ds.trigger_word);
      this.exportTriggerPos.set(ds.trigger_position);
      this.primaryTrigger.set(ds.trigger_word);
    }
  }

  formatJson(obj: unknown): string {
    return JSON.stringify(obj, null, 2);
  }

  async runExport(): Promise<void> {
    const ds = this.api.activeDataset();
    if (!ds) {
      this.api.showToast('Select or create a dataset first', 'warning');
      return;
    }
    this.isExporting.set(true);
    try {
      await this.api.exportDataset(ds.id, {
        image_format: this.imageFormat(),
        max_edge: this.maxEdge(),
        snap_multiple_16: this.snapMultiple16(),
        trigger_word: this.exportTriggerWord().trim() || ds.trigger_word,
        trigger_position: this.exportTriggerPos(),
        create_zip: this.createZipArchive(),
      });
    } finally {
      this.isExporting.set(false);
    }
  }

  toggleRagTagFilter(category: string, slug: string): void {
    this.selectedRagTags.update((prev) => {
      const copy: Record<string, string[]> = { ...prev };
      const list = [...(copy[category] ?? [])];
      const idx = list.indexOf(slug);
      if (idx >= 0) {
        list.splice(idx, 1);
      } else {
        list.push(slug);
      }
      copy[category] = list;
      return copy;
    });
  }

  isRagTagSelected(category: string, slug: string): boolean {
    const map = this.selectedRagTags();
    return (map[category] ?? []).includes(slug);
  }

  async rebuildRagIndex(): Promise<void> {
    this.isIndexing.set(true);
    try {
      const res = await this.api.buildRagIndex({
        dataset_id: this.api.activeDataset()?.id ?? null,
        dense_backend: this.denseBackend(),
      });
      this.indexStats.set(res);
    } finally {
      this.isIndexing.set(false);
    }
  }

  async retrieveOnly(): Promise<void> {
    this.isGenerating.set(true);
    try {
      const results = await this.api.retrieveRagExemplars({
        query: this.sceneQuery(),
        dataset_id: this.api.activeDataset()?.id ?? null,
        tag_filters: this.selectedRagTags(),
        top_k: 4,
        diversity: 0.7,
        retrieval_mode: this.retrievalMode(),
        dense_backend: this.denseBackend(),
      });
      this.retrievedExemplars.set(results);
    } finally {
      this.isGenerating.set(false);
    }
  }

  async synthesizeGroundedPrompts(): Promise<void> {
    this.isGenerating.set(true);
    try {
      const res = await this.api.generateLoRAPrompts({
        query: this.sceneQuery(),
        selected_tags: this.selectedRagTags(),
        dataset_id: this.api.activeDataset()?.id ?? null,
        trigger_word: this.primaryTrigger().trim() || 'ohwx_subject',
        style_trigger: this.secondaryStyleTrigger().trim() || null,
        model: this.api.settings().primary_model || 'google/gemma-4-31b-it',
        top_k: 4,
      });
      this.generatedResult.set(res);
      this.retrievedExemplars.set(res.rag_context ?? []);
    } finally {
      this.isGenerating.set(false);
    }
  }

  copyPromptToClipboard(text: string): void {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      void navigator.clipboard.writeText(text);
    }
    this.api.showToast('Copied Krea 2 prompt to clipboard', 'success');
  }
}
