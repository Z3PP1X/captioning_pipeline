import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { DatePipe, DOCUMENT, NgTemplateOutlet } from '@angular/common';
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
  lucideStar,
  lucidePencil,
  lucideDownload,
  lucideHistory,
  lucideRotateCcw,
  lucideArrowUpRight,
  lucideClipboardList,
  lucideScanSearch,
  lucideQuote,
  lucideX,
  lucideEraser,
  lucideFileText,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmProgressImports } from '@spartan-ng/helm/progress';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmTextareaImports } from '@spartan-ng/helm/textarea';
import { HlmSkeletonImports } from '@spartan-ng/helm/skeleton';
import { BATCH_SIZES, PromptRagService } from '../../core/services/prompt-rag.service';
import { HotkeyAction, HotkeyService } from '../../core/services/hotkey.service';
import {
  ArchetypeKey,
  CharacterProfile,
  CuratedPreset,
  PromptArchetype,
  RagGenerateResult,
  RagHistoryEntry,
  RagItemPreview,
  TaxonomyOption,
} from '../../core/models/prompt-rag.model';
import {
  ARCHETYPE_EXPORT_ORDER,
  buildPromptJsonl,
  buildPromptTxt,
  downloadText,
  exportFilename,
} from '../../core/utils/prompt-export.utils';
import { captionToSeedDescription } from '../../core/utils/prompt-seed.utils';

/** Display order of the taxonomy categories and their lucide icons (backend `icon` is an emoji). */
const CATEGORY_ORDER = ['equipment', 'surroundings', 'position', 'camera_lighting'] as const;
const CATEGORY_ICONS: Record<string, string> = {
  equipment: 'lucideLink',
  surroundings: 'lucideMapPin',
  position: 'lucidePersonStanding',
  camera_lighting: 'lucideCamera',
};
/** B first: it is the recommended format for Krea. Keys 1/2/3 copy the cards in this order. */
const ARCHETYPE_ORDER: readonly ArchetypeKey[] = ARCHETYPE_EXPORT_ORDER;
const COPY_HOTKEYS: readonly HotkeyAction[] = ['copy_prompt_1', 'copy_prompt_2', 'copy_prompt_3'];
type ExportScope = 'b' | 'a' | 'c' | 'all';

const SOURCE_LABELS: Record<string, string> = {
  captions_table: 'captioned frames (captions table)',
  curated: 'curated train/val split',
  finished_merged: 'exported dataset (finished/merged)',
};

/*
 * K4 design pass (design-overhaul-plan §2.7 / §7 K4): page on bg-canvas with white panels, signature H1, one
 * data-driven category block, ink (not red) toggle chips, a sticky Generate bar so the primary action never
 * scrolls away, an "index not built" empty state, the 12 px type floor and helm controls throughout. Panels:
 * `rounded-xl border bg-card shadow-xs`, header row + `p-4` body, overline labels `text-xs font-bold uppercase`.
 */
@Component({
  selector: 'app-prompt-clicker-view',
  imports: [
    DatePipe,
    NgTemplateOutlet,
    NgIcon,
    HlmButtonImports,
    HlmBadgeImports,
    HlmAlertImports,
    HlmProgressImports,
    HlmNativeSelectImports,
    HlmInputImports,
    HlmTextareaImports,
    HlmSkeletonImports,
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
      lucideStar,
      lucidePencil,
      lucideDownload,
      lucideHistory,
      lucideRotateCcw,
      lucideArrowUpRight,
      lucideClipboardList,
      lucideScanSearch,
      lucideQuote,
      lucideX,
      lucideEraser,
      lucideFileText,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'block',
    '(document:keydown)': 'onKeydown($event)',
  },
  template: `
    <div class="flex flex-col h-full w-full overflow-hidden bg-canvas text-foreground">
      <!-- Page header: title + index status | model + trigger -->
      <header class="flex flex-wrap items-end justify-between gap-4 border-b border-border bg-card px-4 lg:px-6 py-4 shrink-0">
        <div class="min-w-0 flex flex-col gap-1">
          <h1 class="font-display font-black uppercase tracking-wide text-2xl leading-none">Prompt Director</h1>
          <p class="text-sm text-muted-foreground flex flex-wrap items-center gap-2">
            @if (rag.isIndexReady()) {
              <span hlmBadge variant="success">
                <ng-icon name="lucideDatabase" aria-hidden="true" />
                Grounded
              </span>
              <span>
                <span class="tabular-nums">{{ rag.indexStatus()?.indexed }}</span> captions from the
                {{ sourceLabel(rag.indexStatus()?.index?.source) }}
              </span>
            } @else if (rag.indexStatus()) {
              <span hlmBadge variant="warning">
                <ng-icon name="lucideTriangleAlert" aria-hidden="true" />
                Not grounded
              </span>
              <span>RAG index not built - prompts are generated without dataset context</span>
            } @else {
              <span class="flex items-center gap-1">
                <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" aria-hidden="true" />
                Checking RAG index...
              </span>
            }
          </p>
        </div>

        <div class="flex flex-wrap items-end gap-3">
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium text-muted-foreground" for="pd-model">Language model</label>
            <hlm-native-select
              size="sm"
              selectId="pd-model"
              class="w-60"
              [value]="rag.selectedModel()"
              (valueChange)="rag.selectedModel.set($event ?? 'google/gemma-4-31b-it')"
            >
              <option value="google/gemma-4-31b-it">Gemma 4 31B (OpenRouter)</option>
              <option value="google/gemma-2-9b-it">Gemma 2 9B (OpenRouter, fast)</option>
              <option value="local">Local Ollama (gemma2:2b)</option>
            </hlm-native-select>
          </div>

          @if (rag.selectedModel() === 'local') {
            <div class="flex flex-col gap-1">
              <label class="text-xs font-medium text-muted-foreground" for="pd-local-url">Ollama URL</label>
              <input
                hlmInput
                id="pd-local-url"
                class="h-8 md:h-8 w-56 text-sm md:text-sm"
                [value]="rag.localUrl()"
                (input)="rag.localUrl.set($any($event.target).value)"
                placeholder="http://localhost:11434"
              />
            </div>
          }

          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium text-muted-foreground" for="pd-trigger">Trigger</label>
            <input
              hlmInput
              id="pd-trigger"
              class="h-8 md:h-8 w-44 text-sm md:text-sm"
              [value]="rag.selectedTrigger()"
              (input)="rag.selectedTrigger.set($any($event.target).value)"
              placeholder="restrained_elegance"
            />
          </div>
        </div>
      </header>

      <!-- Index guidance (empty / building / failed) -->
      @if (rag.indexStatus(); as st) {
        @if (!st.ready || st.build.state === 'running' || st.build.state === 'error') {
          <div class="px-4 lg:px-6 pt-4 shrink-0">
            <div hlmAlert [variant]="st.build.state === 'error' ? 'destructive' : 'default'" class="bg-card">
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
        <div class="px-4 lg:px-6 pt-4 shrink-0">
          <div hlmAlert variant="destructive" class="bg-card">
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
        <!-- Left: ingredients (scrolls on lg; the Generate bar sticks to its bottom) -->
        <section
          class="w-full lg:w-7/12 flex flex-col lg:border-r border-border lg:min-h-0 lg:overflow-y-auto px-4 lg:px-6 pt-4 lg:pt-6"
          aria-labelledby="pd-ingredients-title"
        >
          <div class="flex flex-col gap-4 pb-4">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h2 id="pd-ingredients-title" class="text-xs font-bold uppercase tracking-widest text-muted-foreground">
                Ingredients
                @if (rag.totalSelectedCount() > 0) {
                  <span class="ms-1 normal-case tracking-normal font-medium tabular-nums">· {{ rag.totalSelectedCount() }} tags</span>
                }
              </h2>
              <button hlmBtn variant="ghost" size="sm" type="button" (click)="rag.clearSelections()">
                <ng-icon name="lucideTrash2" class="size-4" aria-hidden="true" />
                Reset all
              </button>
            </div>

            <!-- Presets -->
            @if (rag.presets().length > 0) {
              <div class="flex flex-wrap items-center gap-2" role="group" aria-labelledby="pd-presets-label">
                <span id="pd-presets-label" class="text-xs font-medium text-muted-foreground flex items-center gap-1 pe-1">
                  <ng-icon name="lucideLayers" class="size-3.5" aria-hidden="true" /> Presets
                </span>
                @for (preset of rag.presets(); track preset.id) {
                  <button
                    hlmBtn
                    variant="outline"
                    size="sm"
                    type="button"
                    (click)="onSelectPreset(preset)"
                    [title]="preset.description"
                    [attr.aria-description]="preset.description"
                  >
                    {{ preset.title }}
                  </button>
                }
              </div>
            }

            <!-- Description (seed target) -->
            <div class="rounded-xl border border-border bg-card shadow-xs">
              <div class="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-4">
                <label for="pd-description" class="font-sans text-base font-semibold flex items-center gap-2">
                  <ng-icon name="lucideFileText" class="size-4 text-muted-foreground" aria-hidden="true" />
                  Subject and scene details
                  <span class="text-xs font-normal text-muted-foreground">(optional)</span>
                </label>
                <span class="text-xs text-muted-foreground">Clothing, props, mood</span>
              </div>
              <div class="p-4 flex flex-col gap-3">
                @if (rag.seedFrame(); as seed) {
                  <div class="flex items-center gap-3 rounded-lg border border-border bg-muted/40 p-2">
                    <img
                      [src]="'/api/frames/' + seed.frameId + '/preview'"
                      alt=""
                      class="size-12 shrink-0 rounded-md bg-secondary object-cover"
                      loading="lazy"
                    />
                    <p class="min-w-0 flex-1 text-xs text-muted-foreground">
                      <span class="font-medium text-foreground">Seeded from frame #{{ seed.frameId }}</span>
                      @if (seed.label) {
                        <span class="block truncate">{{ seed.label }}</span>
                      }
                    </p>
                    <button hlmBtn variant="ghost" size="sm" type="button" (click)="rag.showSimilarTo(seed.frameId, seed.label ?? null)">
                      <ng-icon name="lucideScanSearch" class="size-4" aria-hidden="true" />
                      More like this
                    </button>
                    <button
                      hlmBtn
                      variant="ghost"
                      size="icon-sm"
                      type="button"
                      aria-label="Dismiss seed frame"
                      title="Dismiss seed frame"
                      (click)="rag.seedFrame.set(null)"
                    >
                      <ng-icon name="lucideX" class="size-4" aria-hidden="true" />
                    </button>
                  </div>
                }
                <textarea
                  hlmTextarea
                  id="pd-description"
                  rows="3"
                  class="resize-y"
                  [value]="rag.userDescription()"
                  (input)="rag.userDescription.set($any($event.target).value)"
                  placeholder="e.g. athletic adult woman wearing a black silk camisole, intense gaze"
                ></textarea>
              </div>
            </div>

            <!-- Character -->
            <div class="rounded-xl border border-border bg-card shadow-xs">
              <div class="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-4">
                <h3 id="pd-character-title" class="font-sans text-base font-semibold flex items-center gap-2">
                  <ng-icon name="lucideUserRound" class="size-4 text-muted-foreground" aria-hidden="true" />
                  Character
                </h3>
                <span class="text-xs text-muted-foreground">Optional identity for the LoRA subject</span>
              </div>
              <div class="p-4 grid grid-cols-1 md:grid-cols-3 gap-2" role="group" aria-labelledby="pd-character-title">
                @for (c of characterOptions(); track c.id) {
                  @let selected = isCharacterSelected(c.id);
                  <button
                    type="button"
                    [attr.aria-pressed]="selected"
                    (click)="rag.selectCharacter(c.id)"
                    class="flex min-h-11 flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors cursor-pointer outline-none focus-visible:ring-3 focus-visible:ring-ring"
                    [class]="selected ? 'border-secondary bg-muted ring-1 ring-secondary' : 'border-border bg-card hover:bg-muted'"
                  >
                    <span class="text-sm font-medium flex items-center gap-1">
                      {{ c.name }}
                      @if (selected) {
                        <ng-icon name="lucideCheck" class="size-3.5" aria-hidden="true" />
                      }
                    </span>
                    <span class="text-xs text-muted-foreground leading-snug">{{ c.summary }}</span>
                  </button>
                }
              </div>
            </div>

            <!-- Taxonomy categories (one block, data-driven) -->
            @for (catKey of categoryKeys(); track catKey) {
              @if (rag.categories()[catKey]; as cat) {
                @let count = selectedIn(catKey);
                <div class="rounded-xl border border-border bg-card shadow-xs" role="group" [attr.aria-labelledby]="'pd-cat-' + catKey">
                  <div class="flex flex-wrap items-center justify-between gap-2 px-4 pt-4">
                    <h3 [id]="'pd-cat-' + catKey" class="font-sans text-base font-semibold flex items-center gap-2">
                      <ng-icon [name]="categoryIcon(catKey)" class="size-4 text-muted-foreground" aria-hidden="true" />
                      {{ cat.title }}
                      @if (count > 0) {
                        <span hlmBadge variant="muted" class="tabular-nums">{{ count }}<span class="sr-only"> selected</span></span>
                      }
                    </h3>
                    <div class="flex items-center gap-2">
                      <span class="text-xs text-muted-foreground">{{ cat.description }}</span>
                      @if (count > 0) {
                        <button
                          hlmBtn
                          variant="ghost"
                          size="sm"
                          type="button"
                          (click)="rag.clearCategory(catKey)"
                          [attr.aria-label]="'Clear ' + cat.title"
                        >
                          <ng-icon name="lucideEraser" class="size-4" aria-hidden="true" />
                          Clear
                        </button>
                      }
                    </div>
                  </div>
                  <div class="p-4 flex flex-col gap-3">
                    @for (group of getGroupEntries(cat.groups); track group.name) {
                      <div class="flex flex-col gap-2">
                        <div class="text-xs font-bold uppercase tracking-widest text-muted-foreground">{{ group.name }}</div>
                        <div class="flex flex-wrap gap-2">
                          @for (opt of group.items; track opt.id) {
                            @let on = rag.isTagSelected(catKey, opt.id);
                            <button
                              type="button"
                              [attr.aria-pressed]="on"
                              (click)="rag.toggleTag(catKey, opt.id)"
                              class="inline-flex min-h-8 items-center gap-1 rounded-full border px-3 text-xs font-medium transition-colors cursor-pointer outline-none focus-visible:ring-3 focus-visible:ring-ring"
                              [class]="on ? 'bg-secondary text-secondary-foreground border-secondary' : 'bg-card text-foreground border-border hover:bg-muted'"
                            >
                              @if (on) {
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
            } @empty {
              @if (!rag.errorMessage()) {
                <div class="flex flex-col gap-4" aria-busy="true" aria-label="Loading categories">
                  @for (s of [0, 1]; track s) {
                    <div class="rounded-xl border border-border bg-card p-4 shadow-xs flex flex-col gap-3">
                      <hlm-skeleton class="h-5 w-40" />
                      <div class="flex flex-wrap gap-2">
                        <hlm-skeleton class="h-8 w-20 rounded-full" />
                        <hlm-skeleton class="h-8 w-24 rounded-full" />
                        <hlm-skeleton class="h-8 w-16 rounded-full" />
                        <hlm-skeleton class="h-8 w-28 rounded-full" />
                      </div>
                    </div>
                  }
                </div>
              }
            }

            @if (rag.errorMessage()) {
              <div hlmAlert variant="destructive" class="bg-card" role="alert">
                <ng-icon name="lucideCircleAlert" />
                <h2 hlmAlertTitle>Request failed</h2>
                <p hlmAlertDescription>{{ rag.errorMessage() }}</p>
              </div>
            }
          </div>

          <!-- Sticky Generate bar: the one primary action stays visible while the ingredients scroll -->
          <div
            class="sticky bottom-0 z-10 -mx-4 lg:-mx-6 mt-auto border-t border-border bg-card px-4 lg:px-6 py-3 flex flex-col gap-2"
            role="group"
            aria-label="Generate"
          >
            <div class="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-2">
              <button
                hlmBtn
                variant="outline"
                size="sm"
                type="button"
                (click)="onRetrieveOnly()"
                [disabled]="rag.isRetrieving() || rag.isGenerating() || !rag.isIndexReady()"
                [attr.aria-describedby]="!rag.isIndexReady() ? 'pd-preview-reason' : null"
              >
                <ng-icon [name]="rag.isRetrieving() ? 'lucideRefreshCw' : 'lucideEye'" class="size-4" [class.animate-spin]="rag.isRetrieving()" aria-hidden="true" />
                Preview exemplars
              </button>
              <div class="flex items-center gap-2 w-full sm:w-auto">
                <label class="sr-only" for="pd-batch">Number of variations</label>
                <hlm-native-select
                  size="sm"
                  selectId="pd-batch"
                  class="w-32 shrink-0"
                  [value]="'' + rag.batchSize()"
                  (valueChange)="rag.batchSize.set(+($event ?? 1) || 1)"
                >
                  @for (n of batchSizes; track n) {
                    <option [value]="'' + n">{{ n === 1 ? '1 set' : n + ' variations' }}</option>
                  }
                </hlm-native-select>
                <button
                  hlmBtn
                  type="button"
                  class="flex-1 sm:flex-none"
                  (click)="onGenerate()"
                  [disabled]="rag.isGenerating() || rag.isRetrieving()"
                  aria-keyshortcuts="Control+Enter Meta+Enter"
                >
                  <ng-icon [name]="rag.isGenerating() ? 'lucideRefreshCw' : 'lucideSparkles'" class="size-4" [class.animate-spin]="rag.isGenerating()" aria-hidden="true" />
                  {{ rag.isGenerating() ? 'Generating...' : rag.batchSize() > 1 ? 'Generate ' + rag.batchSize() : 'Generate prompts' }}
                  @if (rag.totalSelectedCount() > 0 && !rag.isGenerating()) {
                    <span class="rounded-full bg-primary-foreground px-2 text-xs font-bold text-primary tabular-nums">
                      {{ rag.totalSelectedCount() }}<span class="sr-only"> tags selected</span>
                    </span>
                  }
                </button>
              </div>
            </div>
            <p class="text-xs text-muted-foreground flex flex-wrap items-center justify-between gap-2">
              <span id="pd-preview-reason">
                @if (!rag.isIndexReady()) { Preview needs the RAG index. }
              </span>
              <span>
                <kbd [class]="kbdClass">Ctrl</kbd> / <kbd [class]="kbdClass">Cmd</kbd> + <kbd [class]="kbdClass">Enter</kbd> generates
              </span>
            </p>
          </div>
        </section>

        <!-- Right: results -->
        <section
          class="w-full lg:w-5/12 flex flex-col lg:min-h-0 lg:overflow-y-auto p-4 lg:p-6 gap-4"
          aria-labelledby="pd-results-title"
        >
          <h2 id="pd-results-title" class="sr-only">Results</h2>

          <!-- More like this frame (GET /api/rag/similar) -->
          @if (rag.seedFrameId(); as seedId) {
            <div class="rounded-xl border border-border bg-card shadow-xs" aria-labelledby="pd-similar-title" role="region">
              <div class="flex items-center gap-3 border-b border-border px-4 py-3">
                <img
                  [src]="'/api/frames/' + seedId + '/preview'"
                  alt=""
                  class="size-10 shrink-0 rounded-md bg-secondary object-cover"
                  loading="lazy"
                />
                <div class="min-w-0 flex-1">
                  <h3 id="pd-similar-title" class="font-sans text-base font-semibold flex items-center gap-2">
                    <ng-icon name="lucideScanSearch" class="size-4 text-muted-foreground" aria-hidden="true" />
                    More like frame #{{ seedId }}
                  </h3>
                  <p class="text-xs text-muted-foreground truncate">
                    {{ rag.seedFrameLabel() || 'Captioned frames that look similar' }}
                    @if (rag.similarEmbedder()) {
                      · {{ rag.similarEmbedder() }}
                    }
                  </p>
                </div>
                <button
                  hlmBtn
                  variant="ghost"
                  size="icon-sm"
                  type="button"
                  aria-label="Reload similar frames"
                  title="Reload"
                  (click)="rag.loadSimilar()"
                  [disabled]="rag.isLoadingSimilar()"
                >
                  <ng-icon name="lucideRefreshCw" class="size-4" [class.animate-spin]="rag.isLoadingSimilar()" aria-hidden="true" />
                </button>
                <button hlmBtn variant="ghost" size="icon-sm" type="button" aria-label="Close similar frames" title="Close" (click)="rag.clearSimilar()">
                  <ng-icon name="lucideX" class="size-4" aria-hidden="true" />
                </button>
              </div>
              <div class="p-4 flex flex-col gap-3" aria-live="polite" [attr.aria-busy]="rag.isLoadingSimilar()">
                @if (rag.isLoadingSimilar() && rag.similarResults().length === 0) {
                  @for (s of [0, 1, 2]; track s) {
                    <div class="flex gap-3">
                      <hlm-skeleton class="size-16 shrink-0 rounded-md" />
                      <div class="flex-1 flex flex-col gap-2">
                        <hlm-skeleton class="h-4 w-1/2" />
                        <hlm-skeleton class="h-3 w-full" />
                        <hlm-skeleton class="h-3 w-3/4" />
                      </div>
                    </div>
                  }
                } @else if (rag.similarError(); as err) {
                  <div hlmAlert variant="destructive">
                    <ng-icon name="lucideCircleAlert" />
                    <h4 hlmAlertTitle>Similar-frame search failed</h4>
                    <p hlmAlertDescription>{{ err }}</p>
                  </div>
                } @else if (rag.similarIndexReady() === false) {
                  <div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border p-6 text-center">
                    <ng-icon name="lucideDatabase" class="size-6 text-muted-foreground" aria-hidden="true" />
                    <p class="text-sm font-semibold">Image index not built yet</p>
                    <p class="text-sm text-muted-foreground max-w-xs">
                      "More like this frame" compares image embeddings of the captioned frames. Build the image
                      index on the server, then reload.
                    </p>
                  </div>
                } @else if (rag.similarLoadedFor() === seedId && rag.similarResults().length === 0) {
                  <p class="text-sm text-muted-foreground">No similar captioned frames found.</p>
                } @else {
                  @for (item of rag.similarResults(); track item.id) {
                    <ng-container *ngTemplateOutlet="exemplarCard; context: { $implicit: item }" />
                  }
                }
              </div>
            </div>
          }

          <!-- Prompts -->
          @if (rag.generationResult(); as result) {
            <div class="flex flex-col gap-4" aria-live="polite">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3 class="font-sans text-lg font-bold flex items-center gap-2">
                  <ng-icon name="lucideSparkles" class="size-4 text-muted-foreground" aria-hidden="true" />
                  Prompts
                  @if (rag.batch().length > 1) {
                    <span class="text-sm font-normal text-muted-foreground tabular-nums">
                      {{ rag.activeIndex() + 1 }} of {{ rag.batch().length }}
                    </span>
                  }
                </h3>
                <div class="flex flex-wrap items-center gap-2">
                  @if (result.character) {
                    <span hlmBadge variant="outline">{{ characterName(result.character) }}</span>
                  }
                  <span hlmBadge [variant]="result.grounded ? 'success' : 'warning'">
                    <ng-icon [name]="result.grounded ? 'lucideDatabase' : 'lucideTriangleAlert'" aria-hidden="true" />
                    {{ result.grounded ? 'Grounded' : 'Not grounded' }}
                  </span>
                  <span class="text-xs text-muted-foreground">{{ result.model }}</span>
                  @if (rag.activeEntry(); as entry) {
                    @if (entry.id !== null) {
                      <button
                        hlmBtn
                        variant="ghost"
                        size="icon-sm"
                        type="button"
                        [attr.aria-pressed]="entry.favorite"
                        [attr.aria-label]="entry.favorite ? 'Remove from favourites' : 'Add to favourites'"
                        [title]="entry.favorite ? 'Remove from favourites' : 'Add to favourites'"
                        (click)="rag.toggleFavorite(entry)"
                      >
                        <ng-icon name="lucideStar" class="size-4" [class]="starClass(entry.favorite)" aria-hidden="true" />
                      </button>
                    }
                  }
                </div>
              </div>

              @for (n of result.notices ?? []; track n) {
                <p class="text-xs text-muted-foreground flex items-start gap-1">
                  <ng-icon name="lucideTriangleAlert" class="size-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                  {{ n }}
                </p>
              }

              @if (rag.batch().length > 1) {
                <div class="rounded-xl border border-border bg-card p-2 shadow-xs flex flex-col gap-1" role="list" aria-label="Variations">
                  @for (v of rag.batch(); track $index; let i = $index) {
                    <div
                      role="listitem"
                      class="flex items-start gap-2 rounded-lg border p-2"
                      [class]="i === rag.activeIndex() ? 'border-secondary bg-muted' : 'border-transparent hover:bg-muted'"
                    >
                      <button
                        type="button"
                        class="min-w-0 flex-1 text-left rounded-md cursor-pointer outline-none focus-visible:ring-3 focus-visible:ring-ring"
                        [attr.aria-current]="i === rag.activeIndex() ? 'true' : null"
                        (click)="selectVariation(i)"
                      >
                        <span class="text-xs font-medium text-muted-foreground tabular-nums">Variation {{ i + 1 }}</span>
                        <span class="block text-sm leading-snug line-clamp-2">{{ v.result.archetypes.b.prompt }}</span>
                      </button>
                      <button
                        hlmBtn
                        variant="ghost"
                        size="icon-sm"
                        type="button"
                        [attr.aria-label]="'Copy archetype B of variation ' + (i + 1)"
                        (click)="copyText('var-' + i, v.result.archetypes.b.prompt)"
                      >
                        <ng-icon [name]="copiedKey() === 'var-' + i ? 'lucideCheck' : 'lucideCopy'" class="size-4" aria-hidden="true" />
                      </button>
                    </div>
                  }
                </div>
              }

              @for (key of archetypeKeys; track key; let i = $index) {
                @if (archetype(result, key); as arch) {
                  <article class="rounded-xl border border-border bg-card shadow-xs" [attr.aria-label]="arch.name">
                    <div class="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
                      <div class="flex flex-wrap items-center gap-2">
                        <h4 class="font-sans text-base font-semibold">{{ arch.name }}</h4>
                        @if (key === 'b') {
                          <span hlmBadge variant="secondary">Recommended</span>
                        }
                        <span hlmBadge variant="muted" class="tabular-nums">~{{ arch.token_count }} tok</span>
                        @if (arch.edited) {
                          <span hlmBadge variant="outline">Edited</span>
                        }
                      </div>
                      <div class="flex items-center gap-1">
                        @if (editingKey() !== key) {
                          <button
                            hlmBtn
                            variant="ghost"
                            size="sm"
                            type="button"
                            (click)="startEdit(key, arch.prompt)"
                            [attr.aria-label]="'Edit ' + arch.name"
                          >
                            <ng-icon name="lucidePencil" class="size-4" aria-hidden="true" />
                            Edit
                          </button>
                        }
                        <button
                          hlmBtn
                          variant="ghost"
                          size="sm"
                          type="button"
                          (click)="copyText(key, arch.prompt)"
                          [attr.aria-label]="'Copy ' + arch.name + ' (key ' + (i + 1) + ')'"
                          [attr.aria-keyshortcuts]="'' + (i + 1)"
                        >
                          <ng-icon [name]="copiedKey() === key ? 'lucideCheck' : 'lucideCopy'" class="size-4" aria-hidden="true" />
                          {{ copiedKey() === key ? 'Copied' : 'Copy' }}
                          <kbd class="hidden md:inline" [class]="kbdClass">{{ i + 1 }}</kbd>
                        </button>
                      </div>
                    </div>
                    <div class="p-4 flex flex-col gap-3">
                      @if (editingKey() === key) {
                        <label class="sr-only" [attr.for]="'pd-edit-' + key">Edit {{ arch.name }}</label>
                        <textarea
                          hlmTextarea
                          [id]="'pd-edit-' + key"
                          [rows]="key === 'c' ? 8 : 4"
                          class="text-sm leading-relaxed"
                          [value]="editDraft()"
                          (input)="editDraft.set($any($event.target).value)"
                          (keydown.escape)="cancelEdit()"
                        ></textarea>
                        <div class="flex flex-wrap items-center justify-end gap-2">
                          <button hlmBtn variant="ghost" size="sm" type="button" (click)="cancelEdit()">Cancel</button>
                          <button
                            hlmBtn
                            variant="outline"
                            size="sm"
                            type="button"
                            (click)="applyEdit(key)"
                            [disabled]="isLinting() || !editDraft().trim()"
                          >
                            <ng-icon [name]="isLinting() ? 'lucideRefreshCw' : 'lucideCheck'" class="size-4" [class.animate-spin]="isLinting()" aria-hidden="true" />
                            Re-lint and apply
                          </button>
                        </div>
                      } @else {
                        <p class="rounded-lg bg-muted p-3 text-sm leading-relaxed select-text break-words" [class.max-h-56]="key === 'c'" [class.overflow-y-auto]="key === 'c'">
                          {{ arch.prompt }}
                        </p>
                      }
                      @if (arch.warnings.length > 0) {
                        <ul class="list-disc ps-5 text-xs text-muted-foreground flex flex-col gap-1">
                          @for (w of arch.warnings; track w) {
                            <li>{{ w }}</li>
                          }
                        </ul>
                      }
                    </div>
                  </article>
                }
              }

              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-xs text-muted-foreground">
                  <kbd [class]="kbdClass">1</kbd>
                  <kbd [class]="kbdClass">2</kbd>
                  <kbd [class]="kbdClass">3</kbd>
                  copy B, A, C
                </p>
                <div class="flex flex-wrap items-center gap-1">
                  @if (rag.batch().length > 1) {
                    <button hlmBtn variant="ghost" size="sm" type="button" (click)="copyAll('all-batch', rag.batch())">
                      <ng-icon [name]="copiedKey() === 'all-batch' ? 'lucideCheck' : 'lucideClipboardList'" class="size-4" aria-hidden="true" />
                      Copy all {{ exportScopeLabel() }}
                    </button>
                  }
                  <button hlmBtn variant="ghost" size="sm" type="button" (click)="exportEntries(rag.batch(), 'batch', 'txt')">
                    <ng-icon name="lucideDownload" class="size-4" aria-hidden="true" />
                    .txt
                  </button>
                  <button hlmBtn variant="ghost" size="sm" type="button" (click)="exportEntries(rag.batch(), 'batch', 'jsonl')">
                    <ng-icon name="lucideDownload" class="size-4" aria-hidden="true" />
                    .jsonl
                  </button>
                </div>
              </div>
            </div>
          } @else if (rag.isGenerating()) {
            <div class="rounded-xl border border-border bg-card p-4 shadow-xs flex flex-col gap-3" aria-busy="true" aria-label="Generating prompts">
              <hlm-skeleton class="h-5 w-48" />
              <hlm-skeleton class="h-16 w-full" />
              <hlm-skeleton class="h-16 w-full" />
            </div>
          } @else {
            <div class="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card p-8 text-center">
              <div class="size-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                <ng-icon name="lucideSparkles" class="size-6" aria-hidden="true" />
              </div>
              <h3 class="font-sans text-base font-semibold">No prompts yet</h3>
              <p class="text-sm text-muted-foreground max-w-xs">
                Pick a preset or click tags on the left, then generate. You get three Krea formats to copy.
              </p>
              @if (rag.indexStatus() && !rag.isIndexReady()) {
                <p class="text-xs text-muted-foreground max-w-xs flex items-start gap-1">
                  <ng-icon name="lucideTriangleAlert" class="size-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                  Without the RAG index the prompts are not grounded in your dataset.
                </p>
              }
            </div>
          }

          <!-- Exemplars used (generate / preview) -->
          @if (rag.retrievedContext().length > 0) {
            <div class="rounded-xl border border-border bg-card shadow-xs">
              <div class="flex items-center justify-between border-b border-border px-4 py-3">
                <h3 class="font-sans text-base font-semibold flex items-center gap-2">
                  <ng-icon name="lucideEye" class="size-4 text-muted-foreground" aria-hidden="true" />
                  Dataset exemplars used
                </h3>
                <span class="text-xs text-muted-foreground tabular-nums">{{ rag.retrievedContext().length }}</span>
              </div>
              <div class="p-4 flex flex-col gap-3">
                @for (item of rag.retrievedContext(); track item.id) {
                  <ng-container *ngTemplateOutlet="exemplarCard; context: { $implicit: item }" />
                }
              </div>
            </div>
          }

          <!-- History -->
          <div class="rounded-xl border border-border bg-card shadow-xs" role="region" aria-labelledby="pd-history-title">
            <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
              <h3 id="pd-history-title" class="font-sans text-base font-semibold flex items-center gap-2">
                <ng-icon name="lucideHistory" class="size-4 text-muted-foreground" aria-hidden="true" />
                History
                <span class="text-xs font-normal text-muted-foreground tabular-nums">{{ rag.historyTotal() }}</span>
              </h3>
              <div class="flex items-center gap-1" role="group" aria-label="History filter">
                <button
                  hlmBtn
                  size="sm"
                  type="button"
                  [variant]="rag.historyFavoritesOnly() ? 'ghost' : 'secondary'"
                  [attr.aria-pressed]="!rag.historyFavoritesOnly()"
                  (click)="rag.setHistoryFavoritesOnly(false)"
                >
                  All
                </button>
                <button
                  hlmBtn
                  size="sm"
                  type="button"
                  [variant]="rag.historyFavoritesOnly() ? 'secondary' : 'ghost'"
                  [attr.aria-pressed]="rag.historyFavoritesOnly()"
                  (click)="rag.setHistoryFavoritesOnly(true)"
                >
                  <ng-icon name="lucideStar" class="size-4" aria-hidden="true" />
                  Favourites
                </button>
              </div>
            </div>

            <div class="p-4 flex flex-col gap-3">
              <div class="flex flex-wrap items-center gap-2">
                <label class="text-xs font-medium text-muted-foreground" for="pd-export-scope">Export</label>
                <hlm-native-select
                  size="sm"
                  selectId="pd-export-scope"
                  class="w-36"
                  [value]="exportScope()"
                  (valueChange)="exportScope.set(toExportScope($event))"
                >
                  <option value="b">Archetype B</option>
                  <option value="a">Archetype A</option>
                  <option value="c">Archetype C</option>
                  <option value="all">All archetypes</option>
                </hlm-native-select>
                <button hlmBtn variant="outline" size="sm" type="button" (click)="exportEntries(rag.history(), historyExportName(), 'txt')" [disabled]="rag.history().length === 0">
                  <ng-icon name="lucideDownload" class="size-4" aria-hidden="true" />
                  .txt
                </button>
                <button hlmBtn variant="outline" size="sm" type="button" (click)="exportEntries(rag.history(), historyExportName(), 'jsonl')" [disabled]="rag.history().length === 0">
                  <ng-icon name="lucideDownload" class="size-4" aria-hidden="true" />
                  .jsonl
                </button>
                <button hlmBtn variant="ghost" size="sm" type="button" (click)="copyAll('all-history', rag.history())" [disabled]="rag.history().length === 0">
                  <ng-icon [name]="copiedKey() === 'all-history' ? 'lucideCheck' : 'lucideClipboardList'" class="size-4" aria-hidden="true" />
                  Copy all
                </button>
              </div>

              @if (rag.historyError()) {
                <p class="text-xs text-destructive flex items-center gap-1" role="alert">
                  <ng-icon name="lucideCircleAlert" class="size-3.5" aria-hidden="true" />
                  {{ rag.historyError() }}
                </p>
              }

              @if (rag.history().length === 0) {
                <p class="text-sm text-muted-foreground">
                  {{ rag.historyFavoritesOnly() ? 'No favourites yet - star a prompt set to keep it here.' : 'Generated prompt sets are saved here automatically.' }}
                </p>
              } @else {
                <ul class="flex flex-col gap-2">
                  @for (h of rag.history(); track h.id) {
                    <li class="rounded-lg border p-3 flex flex-col gap-2" [class]="isActiveEntry(h) ? 'border-secondary bg-muted/40' : 'border-border'">
                      <div class="flex items-center justify-between gap-2">
                        <div class="flex flex-wrap items-center gap-2 min-w-0">
                          <span class="text-xs text-muted-foreground tabular-nums">{{ h.created_at * 1000 | date: 'short' }}</span>
                          @if (h.result.character) {
                            <span hlmBadge variant="outline">{{ characterName(h.result.character) }}</span>
                          }
                          @if (tagCount(h) > 0) {
                            <span hlmBadge variant="muted" class="tabular-nums">{{ tagCount(h) }} tags</span>
                          }
                          @if (!h.result.grounded) {
                            <span hlmBadge variant="warning">
                              <ng-icon name="lucideTriangleAlert" aria-hidden="true" />
                              Not grounded
                            </span>
                          }
                        </div>
                        <div class="flex items-center shrink-0">
                          <button
                            hlmBtn
                            variant="ghost"
                            size="icon-sm"
                            type="button"
                            [attr.aria-pressed]="h.favorite"
                            [attr.aria-label]="h.favorite ? 'Remove from favourites' : 'Add to favourites'"
                            [title]="h.favorite ? 'Remove from favourites' : 'Add to favourites'"
                            (click)="rag.toggleFavorite(h)"
                          >
                            <ng-icon name="lucideStar" class="size-4" [class]="starClass(h.favorite)" aria-hidden="true" />
                          </button>
                          <button hlmBtn variant="ghost" size="icon-sm" type="button" aria-label="Restore inputs" title="Restore inputs" (click)="rag.restoreInputs(h)">
                            <ng-icon name="lucideRotateCcw" class="size-4" aria-hidden="true" />
                          </button>
                          <button hlmBtn variant="ghost" size="icon-sm" type="button" aria-label="Open prompts" title="Open prompts" (click)="openEntry(h)">
                            <ng-icon name="lucideArrowUpRight" class="size-4" aria-hidden="true" />
                          </button>
                          @if (!h.favorite) {
                            <button hlmBtn variant="ghost" size="icon-sm" type="button" aria-label="Delete from history" title="Delete" (click)="rag.deleteEntry(h)">
                              <ng-icon name="lucideTrash2" class="size-4" aria-hidden="true" />
                            </button>
                          }
                        </div>
                      </div>
                      <p class="text-sm leading-snug line-clamp-2 select-text" [title]="h.result.archetypes.b.prompt">{{ h.result.archetypes.b.prompt }}</p>
                      @if (h.inputs.user_description) {
                        <p class="text-xs text-muted-foreground line-clamp-1">{{ h.inputs.user_description }}</p>
                      }
                    </li>
                  }
                </ul>
                @if (rag.historyTotal() > rag.history().length) {
                  <p class="text-xs text-muted-foreground tabular-nums">
                    Showing the latest {{ rag.history().length }} of {{ rag.historyTotal() }}.
                  </p>
                }
              }
            </div>
          </div>

          @if (rag.indexStatus()?.index; as idx) {
            <p class="text-xs text-muted-foreground">
              Index built {{ idx.built_at * 1000 | date: 'short' }} ({{ idx.embedding_model }}).
              <button hlmBtn variant="link" size="sm" type="button" class="h-auto p-0 text-xs" (click)="rag.buildIndex()" [disabled]="rag.isIndexBuilding()">
                Rebuild
              </button>
            </p>
          }
        </section>
      </div>
    </div>

    <!-- One exemplar row: retrieve / generate context and "more like this frame" share the same shape -->
    <ng-template #exemplarCard let-item>
      <div class="flex gap-3 rounded-lg border border-border p-3">
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
        <div class="min-w-0 flex-1 flex flex-col gap-1">
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs font-medium truncate select-text" [title]="item.stem">{{ item.stem }}</span>
            <span class="text-xs text-muted-foreground tabular-nums shrink-0" title="Relevance (0-1)">{{ item.score.toFixed(2) }}</span>
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
          <div class="flex flex-wrap items-center gap-1">
            <button
              hlmBtn
              variant="ghost"
              size="sm"
              type="button"
              (click)="useExemplarAsDescription(item)"
              [attr.aria-label]="'Use the caption of ' + item.stem + ' as the description'"
            >
              <ng-icon name="lucideQuote" class="size-4" aria-hidden="true" />
              Use as description
            </button>
            @if (item.frame_id) {
              <button
                hlmBtn
                variant="ghost"
                size="sm"
                type="button"
                (click)="rag.showSimilarTo(item.frame_id, item.stem)"
                [attr.aria-label]="'More like ' + item.stem"
              >
                <ng-icon name="lucideScanSearch" class="size-4" aria-hidden="true" />
                More like this
              </button>
            }
          </div>
        </div>
      </div>
    </ng-template>
  `,
})
export class PromptClickerViewComponent implements OnInit, OnDestroy {
  readonly rag = inject(PromptRagService);
  private readonly hotkeys = inject(HotkeyService);
  private readonly document = inject(DOCUMENT);

  readonly archetypeKeys = ARCHETYPE_ORDER;
  readonly batchSizes = BATCH_SIZES;
  /** Keyboard hint style (no Spartan kbd primitive is vendored yet). */
  readonly kbdClass = 'rounded-sm border border-border bg-muted px-1 font-sans text-xs tabular-nums';
  /** Archetype key, `var-<i>` (variation row) or `all-*` (copy all) that was just copied. */
  readonly copiedKey = signal<string | null>(null);
  readonly failedPreviews = signal<ReadonlySet<number>>(new Set());
  readonly editingKey = signal<ArchetypeKey | null>(null);
  readonly editDraft = signal<string>('');
  readonly isLinting = signal<boolean>(false);
  readonly exportScope = signal<ExportScope>('b');
  readonly exportScopeLabel = computed(() => (this.exportScope() === 'all' ? 'prompts' : this.exportScope().toUpperCase()));
  private copyTimer: ReturnType<typeof setTimeout> | null = null;
  private unregisterHotkeys: (() => void)[] = [];

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

  constructor() {
    // "More like this frame": load the similar exemplars whenever a new seed frame is requested
    effect(() => {
      const id = this.rag.seedFrameId();
      if (id === null || id === this.rag.similarLoadedFor() || this.rag.isLoadingSimilar()) return;
      untracked(() => void this.rag.loadSimilar());
    });
  }

  async ngOnInit(): Promise<void> {
    // 1/2/3 copy the archetype cards; registered only while the Prompt Director is mounted
    this.unregisterHotkeys = COPY_HOTKEYS.map((action, i) =>
      this.hotkeys.register(action, () => this.copyByIndex(i)),
    );
    await Promise.all([this.rag.loadMetadata(), this.rag.loadStatus(), this.rag.loadHistory()]);
  }

  ngOnDestroy(): void {
    this.rag.stopStatusPolling();
    this.unregisterHotkeys.forEach((off) => off());
    this.unregisterHotkeys = [];
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

  tagCount(entry: RagHistoryEntry): number {
    return Object.values(entry.inputs?.selected_tags ?? {}).reduce((sum, list) => sum + (list?.length ?? 0), 0);
  }

  isCharacterSelected(id: string | null): boolean {
    return (this.rag.selectedCharacter() ?? null) === id;
  }

  characterName(id: string): string {
    return this.rag.characters()[id]?.name ?? id;
  }

  archetype(result: RagGenerateResult, key: ArchetypeKey): PromptArchetype | undefined {
    return result.archetypes?.[key];
  }

  starClass(favorite: boolean): string {
    return favorite ? 'size-4 text-foreground [&_svg]:fill-current' : 'size-4 text-muted-foreground';
  }

  isActiveEntry(entry: RagHistoryEntry): boolean {
    const active = this.rag.activeEntry();
    return !!active && active.id !== null && active.id === entry.id;
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
    this.cancelEdit();
    await this.rag.generatePrompts();
  }

  selectVariation(index: number): void {
    this.cancelEdit();
    this.rag.selectVariation(index);
  }

  openEntry(entry: RagHistoryEntry): void {
    this.cancelEdit();
    this.rag.openEntry(entry);
  }

  /** An exemplar's caption (without the trigger) becomes the description; focus moves to the field. */
  useExemplarAsDescription(item: RagItemPreview): void {
    const text = captionToSeedDescription({ text: item.full_caption, trigger: this.rag.selectedTrigger() });
    if (!text) return;
    this.rag.userDescription.set(text);
    this.rag.seedFrame.set(item.frame_id ? { frameId: item.frame_id, label: item.stem } : null);
    this.document.getElementById('pd-description')?.focus();
  }

  // ------------------------------------------------------------------ inline edit
  startEdit(key: ArchetypeKey, prompt: string): void {
    this.editDraft.set(prompt);
    this.editingKey.set(key);
  }

  cancelEdit(): void {
    this.editingKey.set(null);
    this.editDraft.set('');
  }

  async applyEdit(key: ArchetypeKey): Promise<void> {
    const entry = this.rag.activeEntry();
    const text = this.editDraft().trim();
    if (!entry || !text) return;
    this.isLinting.set(true);
    try {
      const arch = await this.rag.applyEdit(entry, key, text);
      if (arch) this.cancelEdit();
    } finally {
      this.isLinting.set(false);
    }
  }

  // ------------------------------------------------------------------ copy + export
  private copyByIndex(index: number): void {
    const result = this.rag.generationResult();
    const key = ARCHETYPE_ORDER[index];
    const prompt = result && key ? result.archetypes?.[key]?.prompt : undefined;
    if (prompt && this.editingKey() !== key) void this.copyText(key, prompt);
  }

  private scopeKeys(): readonly ArchetypeKey[] {
    const scope = this.exportScope();
    return scope === 'all' ? ARCHETYPE_ORDER : [scope];
  }

  toExportScope(value: string | null | undefined): ExportScope {
    return value === 'a' || value === 'c' || value === 'all' ? value : 'b';
  }

  historyExportName(): string {
    return this.rag.historyFavoritesOnly() ? 'favourites' : 'history';
  }

  async copyAll(id: string, entries: readonly RagHistoryEntry[]): Promise<void> {
    await this.copyText(id, buildPromptTxt(entries, this.scopeKeys()).trimEnd());
  }

  exportEntries(entries: readonly RagHistoryEntry[], suffix: string, ext: 'txt' | 'jsonl'): void {
    if (!entries.length) return;
    const keys = this.scopeKeys();
    const content = ext === 'txt' ? buildPromptTxt(entries, keys) : buildPromptJsonl(entries, keys);
    downloadText(exportFilename(suffix, ext), content, ext === 'txt' ? 'text/plain' : 'application/x-ndjson');
  }

  async copyText(id: string, text: string): Promise<void> {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      this.rag.errorMessage.set('Clipboard not available - select the prompt text and copy it manually.');
      return;
    }
    this.copiedKey.set(id);
    if (this.copyTimer) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copiedKey.set(null), 2000);
  }
}
