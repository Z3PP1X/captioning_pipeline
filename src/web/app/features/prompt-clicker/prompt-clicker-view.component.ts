import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideSparkles,
  lucideCopy,
  lucideCheck,
  lucideRefreshCw,
  lucideTrash2,
  lucideExternalLink,
  lucideInfo,
  lucideSliders,
  lucideLayers,
  lucideChevronDown,
  lucideChevronUp,
  lucideEye,
  lucideCheckCircle2,
  lucideAlertCircle,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { PromptRagService } from '../../core/services/prompt-rag.service';
import { CuratedPreset, PromptArchetype } from '../../core/models/prompt-rag.model';

@Component({
  selector: 'app-prompt-clicker-view',
  imports: [CommonModule, FormsModule, NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideSparkles,
      lucideCopy,
      lucideCheck,
      lucideRefreshCw,
      lucideTrash2,
      lucideExternalLink,
      lucideInfo,
      lucideSliders,
      lucideLayers,
      lucideChevronDown,
      lucideChevronUp,
      lucideEye,
      lucideCheckCircle2,
      lucideAlertCircle,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col h-full w-full overflow-hidden bg-background text-foreground select-none">
      <!-- Top Bar: Title, Model Selection, Presets -->
      <header class="flex flex-wrap items-center justify-between gap-4 border-b border-border bg-card px-6 py-3 shrink-0">
        <div class="flex items-center gap-3">
          <div class="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20 shadow-xs">
            <ng-icon name="lucideSparkles" class="size-5" />
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h1 class="font-sans font-black text-base md:text-lg tracking-tight text-foreground">
                Prompt Director & RAG Clicker
              </h1>
              <span hlmBadge variant="outline" class="text-[10px] uppercase font-bold tracking-wider text-primary border-primary/30">
                Krea 2 Grounded
              </span>
            </div>
            <p class="text-xs text-muted-foreground">
              2,500 curated image captions indexed across 4 descriptive domains
            </p>
          </div>
        </div>

        <!-- Presets & Settings -->
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <!-- Preset Selector -->
          <div class="flex items-center gap-1.5 bg-muted/50 rounded-lg p-1 border border-border">
            <span class="text-[11px] font-medium text-muted-foreground px-1.5 flex items-center gap-1">
              <ng-icon name="lucideLayers" class="size-3.5" /> Presets:
            </span>
            @for (preset of ragService.presets(); track preset.id) {
              <button
                hlmBtn
                variant="ghost"
                size="sm"
                class="h-7 text-xs px-2.5 rounded-md hover:bg-card hover:text-foreground transition-all cursor-pointer"
                (click)="onSelectPreset(preset)"
                [title]="preset.description"
              >
                {{ preset.title }}
              </button>
            }
          </div>

          <!-- Model Switcher -->
          <select
            [ngModel]="ragService.selectedModel()"
            (ngModelChange)="ragService.selectedModel.set($event)"
            class="h-8 rounded-lg border border-input bg-card px-2.5 text-xs text-foreground focus:ring-1 focus:ring-ring font-medium"
          >
            <option value="google/gemma-4-31b-it">Gemma 4 31B (OpenRouter)</option>
            <option value="google/gemma-2-9b-it">Gemma 2 9B (OpenRouter - Fast)</option>
            <option value="local">Local Ollama (gemma2:2b)</option>
          </select>

          <!-- Trigger Word Input -->
          <div class="flex items-center gap-1 bg-card rounded-lg border border-input px-2 h-8">
            <span class="text-[10px] text-muted-foreground font-mono">Trigger:</span>
            <input
              type="text"
              [ngModel]="ragService.selectedTrigger()"
              (ngModelChange)="ragService.selectedTrigger.set($event)"
              placeholder="restrained_elegance"
              class="w-32 bg-transparent text-xs text-foreground focus:outline-hidden font-mono"
            />
          </div>
        </div>
      </header>

      <!-- Main Workspace: Split 2-Column Grid -->
      <div class="flex flex-1 min-h-0 overflow-hidden">
        <!-- Left Column: Category Clicker & Prompt Idea (Scrollable) -->
        <div class="w-full lg:w-7/12 flex flex-col border-r border-border min-h-0 overflow-y-auto p-4 md:p-6 space-y-5">
          <!-- Categories Accordion / Cards -->
          <div class="space-y-4">
            <!-- 0. Character Selection -->
            <div class="rounded-xl border border-border bg-card p-4 shadow-2xs">
              <div class="flex items-center justify-between pb-2 border-b border-border/50 mb-3">
                <div class="flex items-center gap-2">
                  <span class="text-base">👤</span>
                  <h3 class="font-sans font-bold text-sm text-foreground">Character Identity</h3>
                </div>
                <span class="text-[11px] text-muted-foreground">Select a character model or use anonymous subject</span>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5">
                <!-- Anonymous / Generic -->
                <button
                  type="button"
                  (click)="ragService.selectCharacter(null)"
                  class="flex flex-col items-start gap-1 p-2.5 rounded-lg border text-left transition-all cursor-pointer"
                  [ngClass]="
                    !ragService.selectedCharacter()
                      ? 'bg-primary/10 border-primary text-primary ring-1 ring-primary/40'
                      : 'bg-muted/30 border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                  "
                >
                  <div class="flex items-center justify-between w-full">
                    <span class="text-xs font-semibold flex items-center gap-1.5">🎭 Anonymous</span>
                    @if (!ragService.selectedCharacter()) {
                      <span class="text-[9px] bg-primary/20 text-primary px-1.5 py-0.2 rounded font-mono font-medium">Selected</span>
                    }
                  </div>
                  <p class="text-[11px] opacity-80 leading-snug">Default generic adult subjects</p>
                </button>

                <!-- Sara -->
                <button
                  type="button"
                  (click)="ragService.selectCharacter('sara')"
                  class="flex flex-col items-start gap-1 p-2.5 rounded-lg border text-left transition-all cursor-pointer"
                  [ngClass]="
                    ragService.selectedCharacter() === 'sara'
                      ? 'bg-primary/10 border-primary text-primary ring-1 ring-primary/40'
                      : 'bg-muted/30 border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                  "
                >
                  <div class="flex items-center justify-between w-full">
                    <span class="text-xs font-semibold flex items-center gap-1.5">👩 Sara <span class="text-[10px] px-1 py-0.2 bg-muted rounded font-normal">21yo</span></span>
                    @if (ragService.selectedCharacter() === 'sara') {
                      <span class="text-[9px] bg-primary/20 text-primary px-1.5 py-0.2 rounded font-mono font-medium">Selected</span>
                    }
                  </div>
                  <p class="text-[11px] opacity-80 leading-snug">21yo woman, brown hair, dark brown eyes</p>
                </button>

                <!-- Lene -->
                <button
                  type="button"
                  (click)="ragService.selectCharacter('lene')"
                  class="flex flex-col items-start gap-1 p-2.5 rounded-lg border text-left transition-all cursor-pointer"
                  [ngClass]="
                    ragService.selectedCharacter() === 'lene'
                      ? 'bg-primary/10 border-primary text-primary ring-1 ring-primary/40'
                      : 'bg-muted/30 border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                  "
                >
                  <div class="flex items-center justify-between w-full">
                    <span class="text-xs font-semibold flex items-center gap-1.5">👧 Lene <span class="text-[10px] px-1 py-0.2 bg-muted rounded font-normal">21yo petite</span></span>
                    @if (ragService.selectedCharacter() === 'lene') {
                      <span class="text-[9px] bg-primary/20 text-primary px-1.5 py-0.2 rounded font-mono font-medium">Selected</span>
                    }
                  </div>
                  <p class="text-[11px] opacity-80 leading-snug">21yo petite woman, dark brown hair, dark brown eyes</p>
                </button>
              </div>
            </div>

            <!-- 1. Equipment Category -->
            @if (ragService.categories()['equipment']; as cat) {
              <div class="rounded-xl border border-border bg-card p-4 shadow-2xs">
                <div class="flex items-center justify-between pb-2 border-b border-border/50 mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-base">{{ cat.icon }}</span>
                    <h3 class="font-sans font-bold text-sm text-foreground">{{ cat.title }}</h3>
                  </div>
                  <span class="text-[11px] text-muted-foreground">{{ cat.description }}</span>
                </div>

                <div class="space-y-3">
                  @for (group of getGroupEntries(cat.groups); track group.name) {
                    <div>
                      <div class="text-[10px] uppercase font-bold tracking-wider text-muted-foreground/80 mb-1.5">
                        {{ group.name }}
                      </div>
                      <div class="flex flex-wrap gap-1.5">
                        @for (opt of group.items; track opt.id) {
                          <button
                            type="button"
                            (click)="ragService.toggleTag('equipment', opt.id)"
                            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all cursor-pointer border"
                            [ngClass]="
                              ragService.isTagSelected('equipment', opt.id)
                                ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                                : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
                            "
                          >
                            @if (ragService.isTagSelected('equipment', opt.id)) {
                              <ng-icon name="lucideCheck" class="size-3" />
                            }
                            <span>{{ opt.label }}</span>
                          </button>
                        }
                      </div>
                    </div>
                  }
                </div>
              </div>
            }

            <!-- 2. Surroundings Category -->
            @if (ragService.categories()['surroundings']; as cat) {
              <div class="rounded-xl border border-border bg-card p-4 shadow-2xs">
                <div class="flex items-center justify-between pb-2 border-b border-border/50 mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-base">{{ cat.icon }}</span>
                    <h3 class="font-sans font-bold text-sm text-foreground">{{ cat.title }}</h3>
                  </div>
                  <span class="text-[11px] text-muted-foreground">{{ cat.description }}</span>
                </div>

                <div class="space-y-3">
                  @for (group of getGroupEntries(cat.groups); track group.name) {
                    <div class="flex flex-wrap gap-1.5">
                      @for (opt of group.items; track opt.id) {
                        <button
                          type="button"
                          (click)="ragService.toggleTag('surroundings', opt.id)"
                          class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all cursor-pointer border"
                          [ngClass]="
                            ragService.isTagSelected('surroundings', opt.id)
                              ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                              : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
                          "
                        >
                          @if (ragService.isTagSelected('surroundings', opt.id)) {
                            <ng-icon name="lucideCheck" class="size-3" />
                          }
                          <span>{{ opt.label }}</span>
                        </button>
                      }
                    </div>
                  }
                </div>
              </div>
            }

            <!-- 3. Position Category -->
            @if (ragService.categories()['position']; as cat) {
              <div class="rounded-xl border border-border bg-card p-4 shadow-2xs">
                <div class="flex items-center justify-between pb-2 border-b border-border/50 mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-base">{{ cat.icon }}</span>
                    <h3 class="font-sans font-bold text-sm text-foreground">{{ cat.title }}</h3>
                  </div>
                  <span class="text-[11px] text-muted-foreground">{{ cat.description }}</span>
                </div>

                <div class="space-y-3">
                  @for (group of getGroupEntries(cat.groups); track group.name) {
                    <div class="flex flex-wrap gap-1.5">
                      @for (opt of group.items; track opt.id) {
                        <button
                          type="button"
                          (click)="ragService.toggleTag('position', opt.id)"
                          class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all cursor-pointer border"
                          [ngClass]="
                            ragService.isTagSelected('position', opt.id)
                              ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                              : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
                          "
                        >
                          @if (ragService.isTagSelected('position', opt.id)) {
                            <ng-icon name="lucideCheck" class="size-3" />
                          }
                          <span>{{ opt.label }}</span>
                        </button>
                      }
                    </div>
                  }
                </div>
              </div>
            }

            <!-- 4. Camera & Lighting Category -->
            @if (ragService.categories()['camera_lighting']; as cat) {
              <div class="rounded-xl border border-border bg-card p-4 shadow-2xs">
                <div class="flex items-center justify-between pb-2 border-b border-border/50 mb-3">
                  <div class="flex items-center gap-2">
                    <span class="text-base">{{ cat.icon }}</span>
                    <h3 class="font-sans font-bold text-sm text-foreground">{{ cat.title }}</h3>
                  </div>
                  <span class="text-[11px] text-muted-foreground">{{ cat.description }}</span>
                </div>

                <div class="space-y-3">
                  @for (group of getGroupEntries(cat.groups); track group.name) {
                    <div>
                      <div class="text-[10px] uppercase font-bold tracking-wider text-muted-foreground/80 mb-1.5">
                        {{ group.name }}
                      </div>
                      <div class="flex flex-wrap gap-1.5">
                        @for (opt of group.items; track opt.id) {
                          <button
                            type="button"
                            (click)="ragService.toggleTag('camera_lighting', opt.id)"
                            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all cursor-pointer border"
                            [ngClass]="
                              ragService.isTagSelected('camera_lighting', opt.id)
                                ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                                : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
                            "
                          >
                            @if (ragService.isTagSelected('camera_lighting', opt.id)) {
                              <ng-icon name="lucideCheck" class="size-3" />
                            }
                            <span>{{ opt.label }}</span>
                          </button>
                        }
                      </div>
                    </div>
                  }
                </div>
              </div>
            }
          </div>

          <!-- Freeform Custom Idea Textarea -->
          <div class="rounded-xl border border-border bg-card p-4 shadow-2xs space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-xs font-bold text-foreground flex items-center gap-1.5">
                <span>Subject & Scene Details (Optional)</span>
              </label>
              <span class="text-[10px] text-muted-foreground">Demographics, hair, clothing, specific props</span>
            </div>
            <textarea
              rows="3"
              [ngModel]="ragService.userDescription()"
              (ngModelChange)="ragService.userDescription.set($event)"
              placeholder="e.g., athletic adult woman with silver hair wearing a black silk camisole, intense gaze..."
              class="w-full rounded-lg border border-input bg-background p-3 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-hidden focus:ring-1 focus:ring-ring resize-none font-sans"
            ></textarea>
          </div>

          <!-- Action Buttons Bar -->
          <div class="flex items-center justify-between gap-3 pt-2">
            <div class="flex items-center gap-2">
              <button
                hlmBtn
                variant="ghost"
                size="sm"
                (click)="ragService.clearSelections()"
                class="cursor-pointer text-xs text-muted-foreground hover:text-destructive gap-1.5"
              >
                <ng-icon name="lucideTrash2" class="size-3.5" />
                <span>Reset</span>
              </button>

              <button
                hlmBtn
                variant="outline"
                size="sm"
                (click)="onRetrieveOnly()"
                [disabled]="ragService.isRetrieving() || ragService.isGenerating()"
                class="cursor-pointer text-xs gap-1.5"
              >
                @if (ragService.isRetrieving()) {
                  <ng-icon name="lucideRefreshCw" class="size-3.5 animate-spin" />
                  <span>Retrieving...</span>
                } @else {
                  <ng-icon name="lucideEye" class="size-3.5" />
                  <span>Preview Context Only</span>
                }
              </button>
            </div>

            <!-- Primary Generate Button -->
            <button
              hlmBtn
              variant="default"
              size="default"
              (click)="onGenerate()"
              [disabled]="ragService.isGenerating() || ragService.isRetrieving()"
              class="cursor-pointer text-xs font-bold tracking-wide gap-2 px-6 shadow-md"
            >
              @if (ragService.isGenerating()) {
                <ng-icon name="lucideRefreshCw" class="size-4 animate-spin" />
                <span>Synthesizing Prompts...</span>
              } @else {
                <ng-icon name="lucideSparkles" class="size-4" />
                <span>Generate Prompts (RAG)</span>
                @if (ragService.totalSelectedCount() > 0) {
                  <span class="ml-1 rounded-full bg-primary-foreground/20 px-1.5 py-0.2 text-[10px] font-bold">
                    {{ ragService.totalSelectedCount() }}
                  </span>
                }
              }
            </button>
          </div>

          <!-- Error Alert if any -->
          @if (ragService.errorMessage()) {
            <div class="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive flex items-center gap-2">
              <ng-icon name="lucideAlertCircle" class="size-4 shrink-0" />
              <span>{{ ragService.errorMessage() }}</span>
            </div>
          }
        </div>

        <!-- Right Column: Generated Prompts & Retrieved Context Preview -->
        <div class="w-full lg:w-5/12 flex flex-col min-h-0 overflow-y-auto p-4 md:p-6 space-y-5 bg-card/30">
          <!-- Prompts Output Area -->
          @if (ragService.generationResult(); as result) {
            <div class="space-y-4">
              <div class="flex items-center justify-between">
                <h2 class="text-sm font-bold text-foreground flex items-center gap-1.5">
                  <ng-icon name="lucideSparkles" class="size-4 text-primary" />
                  <span>Synthesized Prompts</span>
                </h2>
                <div class="flex items-center gap-2">
                  @if (result.character) {
                    <span class="text-[10px] font-semibold text-primary bg-primary/10 border border-primary/20 px-2 py-0.5 rounded-md uppercase tracking-wider">
                      Character: {{ result.character }}
                    </span>
                  }
                  <span class="text-[11px] text-muted-foreground font-mono">
                    Model: {{ result.model }}
                  </span>
                </div>
              </div>

              <!-- Archetype Cards -->
              <!-- 1. Archetype A: Tags -->
              <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-2.5">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <span class="font-sans font-bold text-xs text-foreground">
                      {{ result.archetypes.a.name }}
                    </span>
                    <span hlmBadge variant="outline" class="text-[10px] py-0 font-mono">
                      ~{{ result.archetypes.a.token_count }} tok
                    </span>
                  </div>
                  <button
                    hlmBtn
                    variant="ghost"
                    size="sm"
                    class="h-7 text-xs px-2 gap-1 cursor-pointer"
                    (click)="copyToClipboard('a', result.archetypes.a.prompt)"
                  >
                    @if (copiedMap()['a']) {
                      <ng-icon name="lucideCheck" class="size-3.5 text-primary" />
                      <span class="text-primary font-medium">Copied!</span>
                    } @else {
                      <ng-icon name="lucideCopy" class="size-3.5" />
                      <span>Copy</span>
                    }
                  </button>
                </div>
                <div class="p-3 rounded-lg bg-muted/40 border border-border/60 text-xs font-mono leading-relaxed select-text text-foreground">
                  {{ result.archetypes.a.prompt }}
                </div>
                @if (result.archetypes.a.warnings.length > 0) {
                  <div class="text-[10px] text-muted-foreground space-y-0.5">
                    @for (w of result.archetypes.a.warnings; track w) {
                      <div>• {{ w }}</div>
                    }
                  </div>
                }
              </div>

              <!-- 2. Archetype B: Short Anchor -->
              <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-2.5 ring-1 ring-primary/20">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <span class="font-sans font-bold text-xs text-foreground">
                      {{ result.archetypes.b.name }}
                    </span>
                    <span hlmBadge variant="default" class="text-[10px] py-0 font-mono">
                      Recommended
                    </span>
                    <span hlmBadge variant="outline" class="text-[10px] py-0 font-mono">
                      ~{{ result.archetypes.b.token_count }} tok
                    </span>
                  </div>
                  <button
                    hlmBtn
                    variant="ghost"
                    size="sm"
                    class="h-7 text-xs px-2 gap-1 cursor-pointer"
                    (click)="copyToClipboard('b', result.archetypes.b.prompt)"
                  >
                    @if (copiedMap()['b']) {
                      <ng-icon name="lucideCheck" class="size-3.5 text-primary" />
                      <span class="text-primary font-medium">Copied!</span>
                    } @else {
                      <ng-icon name="lucideCopy" class="size-3.5" />
                      <span>Copy</span>
                    }
                  </button>
                </div>
                <div class="p-3 rounded-lg bg-muted/40 border border-border/60 text-xs font-mono leading-relaxed select-text text-foreground">
                  {{ result.archetypes.b.prompt }}
                </div>
                @if (result.archetypes.b.warnings.length > 0) {
                  <div class="text-[10px] text-muted-foreground space-y-0.5">
                    @for (w of result.archetypes.b.warnings; track w) {
                      <div>• {{ w }}</div>
                    }
                  </div>
                }
              </div>

              <!-- 3. Archetype C: Dense Narrative -->
              <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-2.5">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <span class="font-sans font-bold text-xs text-foreground">
                      {{ result.archetypes.c.name }}
                    </span>
                    <span hlmBadge variant="outline" class="text-[10px] py-0 font-mono">
                      ~{{ result.archetypes.c.token_count }} tok
                    </span>
                  </div>
                  <button
                    hlmBtn
                    variant="ghost"
                    size="sm"
                    class="h-7 text-xs px-2 gap-1 cursor-pointer"
                    (click)="copyToClipboard('c', result.archetypes.c.prompt)"
                  >
                    @if (copiedMap()['c']) {
                      <ng-icon name="lucideCheck" class="size-3.5 text-primary" />
                      <span class="text-primary font-medium">Copied!</span>
                    } @else {
                      <ng-icon name="lucideCopy" class="size-3.5" />
                      <span>Copy</span>
                    }
                  </button>
                </div>
                <div class="p-3 rounded-lg bg-muted/40 border border-border/60 text-xs font-mono leading-relaxed select-text text-foreground max-h-48 overflow-y-auto">
                  {{ result.archetypes.c.prompt }}
                </div>
                @if (result.archetypes.c.warnings.length > 0) {
                  <div class="text-[10px] text-muted-foreground space-y-0.5">
                    @for (w of result.archetypes.c.warnings; track w) {
                      <div>• {{ w }}</div>
                    }
                  </div>
                }
              </div>
            </div>
          } @else {
            <!-- Empty state when no prompts generated yet -->
            <div class="flex flex-col items-center justify-center p-8 text-center rounded-xl border border-dashed border-border bg-card/20 space-y-3">
              <div class="size-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                <ng-icon name="lucideSparkles" class="size-6" />
              </div>
              <div>
                <h3 class="font-sans font-bold text-sm text-foreground">Ready to Build Prompts</h3>
                <p class="text-xs text-muted-foreground max-w-xs mt-1">
                  Select your desired equipment, surroundings, stance, and optics on the left, then click Generate.
                </p>
              </div>
            </div>
          }

          <!-- Retrieved Dataset Grounding Context -->
          @if (ragService.retrievedContext().length > 0) {
            <div class="rounded-xl border border-border bg-card p-4 shadow-xs space-y-3">
              <div class="flex items-center justify-between pb-2 border-b border-border/50">
                <div class="flex items-center gap-1.5">
                  <ng-icon name="lucideEye" class="size-4 text-primary" />
                  <h3 class="font-sans font-bold text-xs text-foreground">
                    Retrieved Dataset Exemplars (RAG Context)
                  </h3>
                </div>
                <span class="text-[10px] text-muted-foreground font-mono">
                  {{ ragService.retrievedContext().length }} items
                </span>
              </div>

              <div class="space-y-3">
                @for (item of ragService.retrievedContext(); track item.id) {
                  <div class="p-2.5 rounded-lg bg-muted/30 border border-border/60 space-y-2">
                    <div class="flex items-center gap-3">
                      <!-- Image Thumbnail Preview -->
                      <img
                        [src]="'/api/rag/preview/' + item.id"
                        [alt]="item.stem"
                        class="size-16 rounded-md object-cover bg-black/40 border border-border shrink-0 shadow-2xs"
                        loading="lazy"
                      />
                      <div class="min-w-0 flex-1">
                        <div class="flex items-center justify-between">
                          <span class="text-[11px] font-bold text-foreground truncate max-w-[180px]">
                            {{ item.stem }}
                          </span>
                          <span class="text-[10px] font-mono text-primary font-bold">
                            Score: {{ item.score }}
                          </span>
                        </div>
                        <div class="flex flex-wrap gap-1 mt-1">
                          @for (t of item.matched_tags; track t) {
                            <span class="text-[9px] px-1.5 py-0.2 rounded-sm bg-muted text-muted-foreground border border-border font-mono">
                              {{ t }}
                            </span>
                          }
                        </div>
                      </div>
                    </div>
                    <p class="text-[11px] text-muted-foreground line-clamp-2 italic leading-relaxed select-text">
                      "{{ item.full_caption }}"
                    </p>
                  </div>
                }
              </div>
            </div>
          }
        </div>
      </div>
    </div>
  `,
})
export class PromptClickerViewComponent implements OnInit {
  readonly ragService = inject(PromptRagService);

  readonly copiedMap = signal<Record<string, boolean>>({});

  async ngOnInit(): Promise<void> {
    await this.ragService.loadMetadata();
  }

  getGroupEntries(groups: Record<string, any[]>): { name: string; items: any[] }[] {
    return Object.entries(groups || {}).map(([name, items]) => ({ name, items }));
  }

  onSelectPreset(preset: CuratedPreset): void {
    this.ragService.applyPreset(preset);
  }

  async onRetrieveOnly(): Promise<void> {
    await this.ragService.retrieveContext();
  }

  async onGenerate(): Promise<void> {
    await this.ragService.generatePrompts();
  }

  copyToClipboard(key: string, text: string): void {
    if (!text) return;
    navigator.clipboard.writeText(text);
    const curr = { ...this.copiedMap() };
    curr[key] = true;
    this.copiedMap.set(curr);
    setTimeout(() => {
      const reset = { ...this.copiedMap() };
      reset[key] = false;
      this.copiedMap.set(reset);
    }, 2000);
  }
}
