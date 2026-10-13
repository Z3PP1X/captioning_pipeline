import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideSparkles,
  lucideCopy,
  lucideCheck,
  lucideRefreshCw,
  lucideTrash2,
  lucideEye,
  lucideLayers,
  lucideCircleAlert,
  lucideTriangleAlert,
  lucideDatabase,
  lucideImageOff,
  lucideLink,
  lucideMapPin,
  lucidePersonStanding,
  lucideCamera,
  lucideUserRound,
  lucideTerminal,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmProgressImports } from '@spartan-ng/helm/progress';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmTextareaImports } from '@spartan-ng/helm/textarea';
import { PromptRagService } from '../../core/services/prompt-rag.service';
import {
  CharacterProfile,
  CuratedPreset,
  PromptArchetype,
  RagGenerateResponse,
  TaxonomyOption,
} from '../../core/models/prompt-rag.model';

/** Display order of the taxonomy categories and their lucide icons (backend `icon` is an emoji). */
const CATEGORY_ORDER = ['equipment', 'surroundings', 'position', 'camera_lighting'] as const;
const CATEGORY_ICONS: Record<string, string> = {
  equipment: 'lucideLink',
  surroundings: 'lucideMapPin',
  position: 'lucidePersonStanding',
  camera_lighting: 'lucideCamera',
};
/** B first: it is the recommended format for Krea. */
const ARCHETYPE_ORDER = ['b', 'a', 'c'] as const;
type ArchetypeKey = (typeof ARCHETYPE_ORDER)[number];

const SOURCE_LABELS: Record<string, string> = {
  curated: 'curated train/val split',
  finished_merged: 'exported dataset (finished/merged)',
};

@Component({
  selector: 'app-prompt-clicker-view',
  imports: [
    DatePipe,
    NgIcon,
    HlmButtonImports,
    HlmBadgeImports,
    HlmAlertImports,
    HlmProgressImports,
    HlmNativeSelectImports,
    HlmInputImports,
    HlmTextareaImports,
  ],
  providers: [
    provideIcons({
      lucideSparkles,
      lucideCopy,
      lucideCheck,
      lucideRefreshCw,
      lucideTrash2,
      lucideEye,
      lucideLayers,
      lucideCircleAlert,
      lucideTriangleAlert,
      lucideDatabase,
      lucideImageOff,
      lucideLink,
      lucideMapPin,
      lucidePersonStanding,
      lucideCamera,
      lucideUserRound,
      lucideTerminal,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'block',
    '(document:keydown)': 'onKeydown($event)',
  },
  template: `
    <div class="flex flex-col h-full w-full overflow-hidden bg-canvas text-foreground">
      <!-- Top bar: title + index status, model, trigger -->
      <header class="flex flex-wrap items-center justify-between gap-4 border-b border-border bg-card px-4 md:px-6 py-3 shrink-0">
        <div class="min-w-0">
          <h1 class="font-display font-black uppercase tracking-wide text-2xl leading-none">Prompt Director</h1>
          <p class="mt-1 text-xs text-muted-foreground tracking-wide flex items-center gap-1.5">
            <ng-icon name="lucideDatabase" class="size-3.5" aria-hidden="true" />
            @if (rag.isIndexReady()) {
              <span>
                Grounded in <span class="tabular-nums">{{ rag.indexStatus()?.indexed }}</span> captions
                from the {{ sourceLabel(rag.indexStatus()?.index?.source) }}
              </span>
            } @else if (rag.indexStatus()) {
              <span>RAG index not built - prompts are not grounded in your dataset</span>
            } @else {
              <span>Checking RAG index...</span>
            }
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <label class="sr-only" for="pd-model">Language model</label>
          <hlm-native-select
            size="sm"
            selectId="pd-model"
            [value]="rag.selectedModel()"
            (valueChange)="rag.selectedModel.set($event ?? 'google/gemma-4-31b-it')"
          >
            <option value="google/gemma-4-31b-it">Gemma 4 31B (OpenRouter)</option>
            <option value="google/gemma-2-9b-it">Gemma 2 9B (OpenRouter, fast)</option>
            <option value="local">Local Ollama (gemma2:2b)</option>
          </hlm-native-select>

          @if (rag.selectedModel() === 'local') {
            <label class="sr-only" for="pd-local-url">Ollama URL</label>
            <input
              hlmInput
              id="pd-local-url"
              class="h-8 md:h-8 w-56 text-sm"
              [value]="rag.localUrl()"
              (input)="rag.localUrl.set($any($event.target).value)"
              placeholder="http://localhost:11434"
            />
          }

          <label class="text-xs text-muted-foreground" for="pd-trigger">Trigger</label>
          <input
            hlmInput
            id="pd-trigger"
            class="h-8 md:h-8 w-44 text-sm"
            [value]="rag.selectedTrigger()"
            (input)="rag.selectedTrigger.set($any($event.target).value)"
            placeholder="restrained_elegance"
          />
        </div>
      </header>

      <!-- Index guidance: missing / building / failed -->
      @if (rag.indexStatus(); as st) {
        @if (!st.ready || st.build.state === 'running' || st.build.state === 'error') {
          <div class="px-4 md:px-6 pt-4 shrink-0">
            <div hlmAlert [variant]="st.build.state === 'error' ? 'destructive' : 'default'">
              <ng-icon [name]="st.build.state === 'error' ? 'lucideCircleAlert' : 'lucideDatabase'" />
              <h2 hlmAlertTitle>
                @switch (st.build.state) {
                  @case ('running') { Building RAG index... }
                  @case ('error') { RAG index build failed }
                  @default { RAG index not built yet }
                }
              </h2>
              <div hlmAlertDescription class="space-y-2">
                @if (st.build.state === 'running') {
                  <p>{{ st.build.step }}</p>
                  <hlm-progress class="max-w-md" [value]="st.build.progress * 100" aria-label="Index build progress">
                    <hlm-progress-indicator />
                  </hlm-progress>
                } @else {
                  @if (st.build.state === 'error') {
                    <p>{{ st.build.error }}</p>
                  }
                  @if (!st.ready) {
                    @if (st.sources?.source) {
                      <p>
                        Found <span class="tabular-nums">{{ st.sources.caption_files }}</span> captions in the
                        {{ sourceLabel(st.sources.source) }}. Build the index once (a few minutes on CPU) to ground
                        prompts in real exemplars. Until then prompts are generated without dataset context.
                      </p>
                    } @else {
                      <p>
                        No captioned frames found yet. Caption and export frames first (Curate, then Finished
                        Dataset), optionally run <code class="text-xs">scripts/curate_dataset.py</code>; searched:
                      </p>
                      <ul class="list-disc ps-5 text-xs">
                        @for (p of st.sources?.searched ?? []; track p) {
                          <li class="break-all">{{ p }}</li>
                        }
                      </ul>
                    }
                  }
                  <div class="flex flex-wrap items-center gap-3 pt-1">
                    <button
                      hlmBtn
                      size="sm"
                      variant="outline"
                      type="button"
                      (click)="rag.buildIndex()"
                      [disabled]="!st.sources?.source"
                    >
                      <ng-icon name="lucideDatabase" class="size-4" aria-hidden="true" />
                      {{ st.index ? 'Rebuild index' : 'Build index' }}
                    </button>
                    <span class="text-xs text-muted-foreground flex items-center gap-1">
                      <ng-icon name="lucideTerminal" class="size-3.5" aria-hidden="true" />
                      or in WSL: <code class="select-all">{{ st.build_command }}</code>
                    </span>
                  </div>
                }
              </div>
            </div>
          </div>
        }
      } @else if (rag.statusError()) {
        <div class="px-4 md:px-6 pt-4 shrink-0">
          <div hlmAlert variant="destructive">
            <ng-icon name="lucideCircleAlert" />
            <h2 hlmAlertTitle>Prompt Director backend unavailable</h2>
            <p hlmAlertDescription>
              {{ rag.statusError() }}. Restart the server so it serves the current API (scripts/start_all.sh).
            </p>
          </div>
        </div>
      }

      <!-- Workspace -->
      <div class="flex flex-col lg:flex-row flex-1 min-h-0 overflow-y-auto lg:overflow-hidden">
        <!-- Left: selections -->
        <section
          class="w-full lg:w-7/12 flex flex-col lg:border-r border-border lg:min-h-0 lg:overflow-y-auto p-4 md:p-6 gap-4"
          aria-label="Prompt ingredients"
        >
          <!-- Presets -->
          @if (rag.presets().length > 0) {
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-1 pe-1">
                <ng-icon name="lucideLayers" class="size-3.5" aria-hidden="true" /> Presets
              </span>
              @for (preset of rag.presets(); track preset.id) {
                <button hlmBtn variant="outline" size="sm" type="button" (click)="onSelectPreset(preset)" [title]="preset.description">
                  {{ preset.title }}
                </button>
              }
            </div>
          }

          <!-- Character -->
          <div class="rounded-xl border border-border bg-card p-4 shadow-xs">
            <div class="flex flex-wrap items-baseline justify-between gap-2 pb-3">
              <h3 class="font-sans text-base font-semibold flex items-center gap-2">
                <ng-icon name="lucideUserRound" class="size-4 text-muted-foreground" aria-hidden="true" />
                Character
              </h3>
              <span class="text-xs text-muted-foreground">Optional identity for the LoRA subject</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2" role="radiogroup" aria-label="Character">
              @for (c of characterOptions(); track c.id) {
                <button
                  type="button"
                  role="radio"
                  [attr.aria-checked]="isCharacterSelected(c.id)"
                  (click)="rag.selectCharacter(c.id)"
                  class="flex flex-col items-start gap-0.5 rounded-lg border p-2.5 text-left transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  [class]="isCharacterSelected(c.id) ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'border-border bg-card hover:bg-muted'"
                >
                  <span class="text-sm font-medium flex items-center gap-1.5">
                    {{ c.name }}
                    @if (isCharacterSelected(c.id)) {
                      <ng-icon name="lucideCheck" class="size-3.5 text-primary" aria-hidden="true" />
                    }
                  </span>
                  <span class="text-xs text-muted-foreground leading-snug">{{ c.summary }}</span>
                </button>
              }
            </div>
          </div>

          <!-- Taxonomy categories -->
          @for (catKey of categoryKeys(); track catKey) {
            @if (rag.categories()[catKey]; as cat) {
              <div class="rounded-xl border border-border bg-card p-4 shadow-xs">
                <div class="flex flex-wrap items-baseline justify-between gap-2 pb-3">
                  <h3 class="font-sans text-base font-semibold flex items-center gap-2">
                    <ng-icon [name]="categoryIcon(catKey)" class="size-4 text-muted-foreground" aria-hidden="true" />
                    {{ cat.title }}
                    @if (selectedIn(catKey) > 0) {
                      <span hlmBadge variant="muted" class="tabular-nums">{{ selectedIn(catKey) }}</span>
                    }
                  </h3>
                  <span class="text-xs text-muted-foreground">{{ cat.description }}</span>
                </div>
                <div class="space-y-3">
                  @for (group of getGroupEntries(cat.groups); track group.name) {
                    <div>
                      <div class="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-1.5">{{ group.name }}</div>
                      <div class="flex flex-wrap gap-1.5">
                        @for (opt of group.items; track opt.id) {
                          <button
                            type="button"
                            [attr.aria-pressed]="rag.isTagSelected(catKey, opt.id)"
                            (click)="rag.toggleTag(catKey, opt.id)"
                            class="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            [class]="rag.isTagSelected(catKey, opt.id) ? 'bg-secondary text-secondary-foreground border-secondary' : 'bg-card text-foreground border-border hover:bg-muted'"
                          >
                            @if (rag.isTagSelected(catKey, opt.id)) {
                              <ng-icon name="lucideCheck" class="size-3" aria-hidden="true" />
                            }
                            {{ opt.label }}
                          </button>
                        }
                      </div>
                    </div>
                  }
                </div>
              </div>
            }
          }

          <!-- Free text -->
          <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-2">
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <label for="pd-description" class="text-sm font-medium">Subject and scene details (optional)</label>
              <span class="text-xs text-muted-foreground">Clothing, props, mood</span>
            </div>
            <textarea
              hlmTextarea
              id="pd-description"
              rows="3"
              class="resize-none"
              [value]="rag.userDescription()"
              (input)="rag.userDescription.set($any($event.target).value)"
              placeholder="e.g. athletic adult woman wearing a black silk camisole, intense gaze"
            ></textarea>
          </div>

          <!-- Actions -->
          <div class="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-3">
            <div class="flex items-center gap-2">
              <button hlmBtn variant="ghost" size="sm" type="button" (click)="rag.clearSelections()">
                <ng-icon name="lucideTrash2" class="size-4" aria-hidden="true" />
                Reset
              </button>
              <button
                hlmBtn
                variant="outline"
                size="sm"
                type="button"
                (click)="onRetrieveOnly()"
                [disabled]="rag.isRetrieving() || rag.isGenerating() || !rag.isIndexReady()"
              >
                <ng-icon [name]="rag.isRetrieving() ? 'lucideRefreshCw' : 'lucideEye'" class="size-4" [class.animate-spin]="rag.isRetrieving()" aria-hidden="true" />
                Preview exemplars
              </button>
            </div>
            <button
              hlmBtn
              type="button"
              class="w-full sm:w-auto"
              (click)="onGenerate()"
              [disabled]="rag.isGenerating() || rag.isRetrieving()"
              title="Ctrl/Cmd + Enter"
            >
              <ng-icon [name]="rag.isGenerating() ? 'lucideRefreshCw' : 'lucideSparkles'" class="size-4" [class.animate-spin]="rag.isGenerating()" aria-hidden="true" />
              {{ rag.isGenerating() ? 'Generating...' : 'Generate prompts' }}
              @if (rag.totalSelectedCount() > 0 && !rag.isGenerating()) {
                <span class="rounded-full bg-primary-foreground/20 px-1.5 text-xs tabular-nums">{{ rag.totalSelectedCount() }}</span>
              }
            </button>
          </div>
          <p class="text-xs text-muted-foreground -mt-2 sm:text-right">
            <kbd class="rounded border border-border bg-muted px-1">Ctrl</kbd> /
            <kbd class="rounded border border-border bg-muted px-1">Cmd</kbd> +
            <kbd class="rounded border border-border bg-muted px-1">Enter</kbd> generates
          </p>

          @if (rag.errorMessage()) {
            <div hlmAlert variant="destructive">
              <ng-icon name="lucideCircleAlert" />
              <h2 hlmAlertTitle>Request failed</h2>
              <p hlmAlertDescription>{{ rag.errorMessage() }}</p>
            </div>
          }
        </section>

        <!-- Right: results -->
        <section class="w-full lg:w-5/12 flex flex-col lg:min-h-0 lg:overflow-y-auto p-4 md:p-6 gap-4" aria-label="Generated prompts" aria-live="polite">
          @if (rag.generationResult(); as result) {
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h2 class="font-sans text-lg font-bold flex items-center gap-2">
                <ng-icon name="lucideSparkles" class="size-4 text-primary" aria-hidden="true" />
                Prompts
              </h2>
              <div class="flex flex-wrap items-center gap-1.5">
                @if (result.character) {
                  <span hlmBadge variant="outline">{{ characterName(result.character) }}</span>
                }
                <span hlmBadge [variant]="result.grounded ? 'success' : 'warning'">
                  <ng-icon [name]="result.grounded ? 'lucideDatabase' : 'lucideTriangleAlert'" aria-hidden="true" />
                  {{ result.grounded ? 'Grounded' : 'Not grounded' }}
                </span>
                <span class="text-xs text-muted-foreground">{{ result.model }}</span>
              </div>
            </div>

            @for (n of result.notices ?? []; track n) {
              <p class="text-xs text-muted-foreground flex items-start gap-1.5">
                <ng-icon name="lucideTriangleAlert" class="size-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                {{ n }}
              </p>
            }

            @for (key of archetypeKeys; track key) {
              @if (archetype(result, key); as arch) {
                <div class="rounded-xl border bg-card p-4 shadow-xs space-y-2.5" [class]="key === 'b' ? 'border-primary' : 'border-border'">
                  <div class="flex items-center justify-between gap-2">
                    <div class="flex flex-wrap items-center gap-1.5">
                      <h3 class="font-sans text-base font-semibold">{{ arch.name }}</h3>
                      @if (key === 'b') {
                        <span hlmBadge>Recommended</span>
                      }
                      <span hlmBadge variant="muted" class="tabular-nums">~{{ arch.token_count }} tok</span>
                    </div>
                    <button
                      hlmBtn
                      variant="ghost"
                      size="sm"
                      type="button"
                      (click)="copyToClipboard(key, arch.prompt)"
                      [attr.aria-label]="'Copy ' + arch.name"
                    >
                      <ng-icon [name]="copiedKey() === key ? 'lucideCheck' : 'lucideCopy'" class="size-4" aria-hidden="true" />
                      {{ copiedKey() === key ? 'Copied' : 'Copy' }}
                    </button>
                  </div>
                  <p class="rounded-lg bg-muted p-3 text-sm leading-relaxed select-text break-words" [class.max-h-56]="key === 'c'" [class.overflow-y-auto]="key === 'c'">
                    {{ arch.prompt }}
                  </p>
                  @if (arch.warnings.length > 0) {
                    <ul class="list-disc ps-5 text-xs text-muted-foreground space-y-0.5">
                      @for (w of arch.warnings; track w) {
                        <li>{{ w }}</li>
                      }
                    </ul>
                  }
                </div>
              }
            }
          } @else {
            <div class="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card p-8 text-center">
              <div class="size-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                <ng-icon name="lucideSparkles" class="size-6" aria-hidden="true" />
              </div>
              <h3 class="font-sans text-base font-semibold">No prompts yet</h3>
              <p class="text-sm text-muted-foreground max-w-xs">
                Pick a preset or click tags on the left, then generate. You get three Krea formats to copy.
              </p>
            </div>
          }

          @if (rag.retrievedContext().length > 0) {
            <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-3">
              <div class="flex items-center justify-between">
                <h3 class="font-sans text-base font-semibold flex items-center gap-2">
                  <ng-icon name="lucideEye" class="size-4 text-muted-foreground" aria-hidden="true" />
                  Dataset exemplars used
                </h3>
                <span class="text-xs text-muted-foreground tabular-nums">{{ rag.retrievedContext().length }}</span>
              </div>
              @for (item of rag.retrievedContext(); track item.id) {
                <div class="flex gap-3 rounded-lg border border-border p-2.5">
                  @if (item.img_rel_path && !failedPreviews().has(item.id)) {
                    <img
                      [src]="'/api/rag/preview/' + item.id"
                      [alt]="item.stem"
                      class="size-16 shrink-0 rounded-md object-cover bg-muted"
                      loading="lazy"
                      (error)="markPreviewFailed(item.id)"
                    />
                  } @else {
                    <div class="size-16 shrink-0 rounded-md bg-muted flex items-center justify-center text-muted-foreground">
                      <ng-icon name="lucideImageOff" class="size-5" aria-label="No preview" />
                    </div>
                  }
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-xs font-medium truncate select-text" [title]="item.stem">{{ item.stem }}</span>
                      <span class="text-xs text-muted-foreground tabular-nums shrink-0">{{ item.score.toFixed(2) }}</span>
                    </div>
                    @if (item.matched_tags.length > 0) {
                      <div class="flex flex-wrap gap-1">
                        @for (t of item.matched_tags.slice(0, 4); track t) {
                          <span hlmBadge variant="muted">{{ tagLabel(t) }}</span>
                        }
                        @if (item.matched_tags.length > 4) {
                          <span hlmBadge variant="outline" class="tabular-nums" [title]="tagList(item.matched_tags)">
                            +{{ item.matched_tags.length - 4 }}
                          </span>
                        }
                      </div>
                    }
                    <p class="text-xs text-muted-foreground line-clamp-2 leading-relaxed select-text" [title]="item.full_caption">
                      {{ item.full_caption }}
                    </p>
                  </div>
                </div>
              }
            </div>
          }

          @if (rag.indexStatus()?.index; as idx) {
            <p class="text-xs text-muted-foreground tracking-wide">
              Index built {{ idx.built_at * 1000 | date: 'short' }} ({{ idx.embedding_model }}).
              <button hlmBtn variant="link" size="sm" type="button" class="h-auto p-0 text-xs" (click)="rag.buildIndex()" [disabled]="rag.isIndexBuilding()">
                Rebuild
              </button>
            </p>
          }
        </section>
      </div>
    </div>
  `,
})
export class PromptClickerViewComponent implements OnInit, OnDestroy {
  readonly rag = inject(PromptRagService);

  readonly archetypeKeys = ARCHETYPE_ORDER;
  readonly copiedKey = signal<ArchetypeKey | null>(null);
  readonly failedPreviews = signal<ReadonlySet<number>>(new Set());
  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  /** Known categories first in a fixed order, then any extra ones the backend adds. */
  readonly categoryKeys = computed(() => {
    const keys = Object.keys(this.rag.categories());
    const ordered = CATEGORY_ORDER.filter((k) => keys.includes(k));
    return [...ordered, ...keys.filter((k) => !(CATEGORY_ORDER as readonly string[]).includes(k))];
  });

  readonly characterOptions = computed<{ id: string | null; name: string; summary: string }[]>(() => [
    { id: null, name: 'Anonymous', summary: 'Generic adult subject' },
    ...Object.values(this.rag.characters()).map((c: CharacterProfile) => ({ id: c.id, name: c.name, summary: c.summary })),
  ]);

  private readonly tagLabels = computed(() => {
    const map = new Map<string, string>();
    for (const cat of Object.values(this.rag.categories())) {
      for (const items of Object.values(cat.groups || {})) {
        for (const opt of items) map.set(opt.id, opt.label);
      }
    }
    return map;
  });

  async ngOnInit(): Promise<void> {
    await Promise.all([this.rag.loadMetadata(), this.rag.loadStatus()]);
  }

  ngOnDestroy(): void {
    this.rag.stopStatusPolling();
    if (this.copyTimer) clearTimeout(this.copyTimer);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !this.rag.isGenerating()) {
      event.preventDefault();
      void this.onGenerate();
    }
  }

  getGroupEntries(groups: Record<string, TaxonomyOption[]>): { name: string; items: TaxonomyOption[] }[] {
    return Object.entries(groups || {}).map(([name, items]) => ({ name, items }));
  }

  categoryIcon(key: string): string {
    return CATEGORY_ICONS[key] ?? 'lucideLayers';
  }

  selectedIn(key: string): number {
    return this.rag.selectedTags()[key]?.length ?? 0;
  }

  sourceLabel(source: string | null | undefined): string {
    return (source && SOURCE_LABELS[source]) || 'dataset';
  }

  tagLabel(id: string): string {
    return this.tagLabels().get(id) ?? id;
  }

  tagList(ids: string[]): string {
    return ids.map((id) => this.tagLabel(id)).join(', ');
  }

  isCharacterSelected(id: string | null): boolean {
    return (this.rag.selectedCharacter() ?? null) === id;
  }

  characterName(id: string): string {
    return this.rag.characters()[id]?.name ?? id;
  }

  archetype(result: RagGenerateResponse, key: ArchetypeKey): PromptArchetype | undefined {
    return result.archetypes?.[key];
  }

  markPreviewFailed(id: number): void {
    this.failedPreviews.update((s) => new Set(s).add(id));
  }

  onSelectPreset(preset: CuratedPreset): void {
    this.rag.applyPreset(preset);
  }

  async onRetrieveOnly(): Promise<void> {
    await this.rag.retrieveContext();
  }

  async onGenerate(): Promise<void> {
    await this.rag.generatePrompts();
  }

  async copyToClipboard(key: ArchetypeKey, text: string): Promise<void> {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      this.rag.errorMessage.set('Clipboard not available - select the prompt text and copy it manually.');
      return;
    }
    this.copiedKey.set(key);
    if (this.copyTimer) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copiedKey.set(null), 2000);
  }
}
