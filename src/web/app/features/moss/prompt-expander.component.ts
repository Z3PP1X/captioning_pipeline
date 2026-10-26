import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  model,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideAlertCircle,
  lucideCheck,
  lucideCopy,
  lucideFileText,
  lucideImage,
  lucideLayers,
  lucideLoader2,
  lucideRefreshCw,
  lucideSparkles,
  lucideTag,
  lucideWand2,
} from '@ng-icons/lucide';
import { MossService } from '../../core/services/moss.service';
import { PromptExpandResponse, PromptExemplar } from '../../core/models/moss.model';
import { countWords } from './three-tier-caption-editor.component';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmInputImports } from '@spartan-ng/helm/input';

@Component({
  selector: 'app-prompt-expander',
  imports: [FormsModule, NgIcon, HlmBadgeImports, HlmButtonImports, HlmInputImports],
  providers: [
    provideIcons({
      lucideSparkles,
      lucideLoader2,
      lucideCopy,
      lucideCheck,
      lucideLayers,
      lucideImage,
      lucideTag,
      lucideFileText,
      lucideAlertCircle,
      lucideRefreshCw,
      lucideWand2,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-6 rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm">
      <!-- Header -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div>
          <div class="flex items-center gap-2">
            <h3 class="text-base font-semibold tracking-tight text-foreground flex items-center gap-2">
              <ng-icon name="lucideWand2" class="size-4 text-primary" />
              <span>MOSS Prompt-Expander</span>
            </h3>
            <span hlmBadge variant="outline" class="text-xs font-mono">
              RAG-Grounded
            </span>
          </div>
          <p class="text-xs text-muted-foreground mt-0.5">
            Synthetisiert aus einer kurzen Szenen-Idee und Trainings-Exemplaren einen 3-Tier Prompt (Long, Medium, Short).
          </p>
        </div>

        @if (isExpanding()) {
          <span class="inline-flex items-center gap-1.5 text-xs text-primary font-medium animate-pulse">
            <ng-icon name="lucideLoader2" class="size-3.5 animate-spin" />
            <span>Erweitere Prompt via RAG...</span>
          </span>
        }
      </div>

      <!-- Input Area for User Scene Idea -->
      <div class="flex flex-col gap-2">
        <label for="user-idea-input" class="text-xs font-semibold text-foreground uppercase tracking-wide">
          Szenen-Idee / Prompt-Konzept
        </label>
        <div class="flex flex-col sm:flex-row gap-2">
          <input
            id="user-idea-input"
            hlmInput
            type="text"
            placeholder="z.B. sitzt nachts im Regen an einer Bushaltestelle mit Neonreklame..."
            [ngModel]="userIdea()"
            (ngModelChange)="userIdea.set($event)"
            (keydown.enter)="expandPrompt()"
            class="flex-1 text-sm font-sans"
            [disabled]="isExpanding()"
          />
          <button
            type="button"
            hlmButton
            class="h-9 px-4 gap-2 text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 shrink-0"
            [disabled]="isExpanding() || !userIdea().trim()"
            (click)="expandPrompt()"
          >
            @if (isExpanding()) {
              <ng-icon name="lucideLoader2" class="size-4 animate-spin" />
              <span>Wird erweitert...</span>
            } @else {
              <ng-icon name="lucideSparkles" class="size-4" />
              <span>Prompt erweitern</span>
            }
          </button>
        </div>
      </div>

      <!-- Error Alert -->
      @if (errorMessage()) {
        <div class="rounded-lg border border-destructive/40 bg-destructive/10 p-3 flex items-start gap-2.5 text-destructive text-xs">
          <ng-icon name="lucideAlertCircle" class="size-4 shrink-0 mt-0.5" />
          <div class="flex-1 flex flex-col gap-1">
            <span class="font-semibold">Fehler bei der Prompterweiterung</span>
            <span>{{ errorMessage() }}</span>
          </div>
          <button
            type="button"
            hlmButton
            variant="ghost"
            size="sm"
            class="h-6 px-2 text-[11px] text-destructive hover:bg-destructive/15"
            (click)="expandPrompt()"
          >
            Erneut versuchen
          </button>
        </div>
      }

      <!-- Result Section: 3-Tier Multi-Prompt Cards -->
      @if (expandedResult(); as res) {
        <div class="flex flex-col gap-4 border-t border-border/60 pt-4">
          <div class="flex items-center justify-between">
            <h4 class="text-sm font-semibold text-foreground flex items-center gap-2">
              <ng-icon name="lucideLayers" class="size-4 text-emerald-500" />
              <span>Generierter 3-Tier MOSS Prompt</span>
            </h4>
            <span class="text-xs text-muted-foreground font-mono">
              1-Klick-Kopieren bereit
            </span>
          </div>

          <div class="grid grid-cols-1 gap-3">
            <!-- 1. LONG TIER -->
            <div class="flex flex-col gap-2 rounded-lg border border-border bg-canvas/40 p-4 transition-colors hover:border-border/80">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span hlmBadge variant="outline" class="font-mono text-xs uppercase font-bold text-foreground">
                    Tier 1: LONG
                  </span>
                  <span class="text-xs text-muted-foreground font-mono">
                    {{ longWordCount() }} Wörter
                  </span>
                </div>

                <button
                  type="button"
                  hlmButton
                  size="sm"
                  variant="outline"
                  class="h-7 px-2.5 text-xs gap-1.5"
                  (click)="copyText(res.long, 'long')"
                >
                  <ng-icon [name]="copiedTier() === 'long' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
                  <span>{{ copiedTier() === 'long' ? 'Kopiert!' : 'Kopieren' }}</span>
                </button>
              </div>

              <p class="text-xs font-sans leading-relaxed text-foreground select-all bg-card p-3 rounded-md border border-border/60">
                {{ res.long }}
              </p>
            </div>

            <!-- 2. MEDIUM TIER -->
            <div class="flex flex-col gap-2 rounded-lg border border-border bg-canvas/40 p-4 transition-colors hover:border-border/80">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span hlmBadge variant="outline" class="font-mono text-xs uppercase font-bold text-foreground">
                    Tier 2: MEDIUM
                  </span>
                  <span class="text-xs text-muted-foreground font-mono">
                    {{ mediumWordCount() }} Wörter
                  </span>
                </div>

                <button
                  type="button"
                  hlmButton
                  size="sm"
                  variant="outline"
                  class="h-7 px-2.5 text-xs gap-1.5"
                  (click)="copyText(res.medium, 'medium')"
                >
                  <ng-icon [name]="copiedTier() === 'medium' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
                  <span>{{ copiedTier() === 'medium' ? 'Kopiert!' : 'Kopieren' }}</span>
                </button>
              </div>

              <p class="text-xs font-sans leading-relaxed text-foreground select-all bg-card p-3 rounded-md border border-border/60">
                {{ res.medium }}
              </p>
            </div>

            <!-- 3. SHORT TIER -->
            <div class="flex flex-col gap-2 rounded-lg border border-border bg-canvas/40 p-4 transition-colors hover:border-border/80">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span hlmBadge variant="outline" class="font-mono text-xs uppercase font-bold text-foreground">
                    Tier 3: SHORT
                  </span>
                  <span class="text-xs text-muted-foreground font-mono">
                    {{ shortWordCount() }} Wörter
                  </span>
                </div>

                <button
                  type="button"
                  hlmButton
                  size="sm"
                  variant="outline"
                  class="h-7 px-2.5 text-xs gap-1.5"
                  (click)="copyText(res.short, 'short')"
                >
                  <ng-icon [name]="copiedTier() === 'short' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
                  <span>{{ copiedTier() === 'short' ? 'Kopiert!' : 'Kopieren' }}</span>
                </button>
              </div>

              <p class="text-xs font-sans leading-relaxed text-foreground select-all bg-card p-3 rounded-md border border-border/60">
                {{ res.short }}
              </p>
            </div>
          </div>

          <!-- Retrieved RAG Exemplars Cards -->
          @if (res.exemplars && res.exemplars.length > 0) {
            <div class="flex flex-col gap-3 pt-2">
              <div class="flex items-center gap-2">
                <h5 class="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Verwendete Dataset-Exemplare ({{ res.exemplars.length }})
                </h5>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                @for (ex of res.exemplars; track ex.stem || ex.id) {
                  <div class="flex flex-col gap-2 rounded-lg border border-border bg-card p-3 shadow-2xs">
                    <div class="flex items-center justify-between text-xs">
                      <span class="font-mono font-medium text-foreground truncate max-w-[160px]">
                        {{ ex.stem || ('Item #' + ex.id) }}
                      </span>
                      @if (ex.score !== undefined) {
                        <span class="rounded bg-muted px-1.5 py-0.2 font-mono text-[10px] text-muted-foreground">
                          Score: {{ (ex.score * 100).toFixed(0) }}%
                        </span>
                      }
                    </div>

                    @if (ex.matched_tags && ex.matched_tags.length > 0) {
                      <div class="flex flex-wrap gap-1">
                        @for (tag of ex.matched_tags; track tag) {
                          <span class="rounded-xs bg-primary/10 px-1 py-0.2 text-[10px] font-mono text-primary">
                            {{ tag }}
                          </span>
                        }
                      </div>
                    }

                    <p class="text-[11px] text-muted-foreground line-clamp-3 leading-relaxed">
                      {{ ex.full_caption }}
                    </p>
                  </div>
                }
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
})
export class PromptExpanderComponent {
  private readonly mossService = inject(MossService);

  readonly userIdea = model<string>('');
  readonly datasetId = input<string | null>(null);
  readonly triggerWord = input<string>('ohwx_subject');
  readonly classNoun = input<string>('woman');
  readonly trainingMode = input<string>('CHARACTER');

  readonly isExpanding = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly expandedResult = signal<PromptExpandResponse | null>(null);
  readonly copiedTier = signal<'long' | 'medium' | 'short' | null>(null);

  readonly longWordCount = computed(() => {
    const res = this.expandedResult();
    return res ? countWords(res.long) : 0;
  });

  readonly mediumWordCount = computed(() => {
    const res = this.expandedResult();
    return res ? countWords(res.medium) : 0;
  });

  readonly shortWordCount = computed(() => {
    const res = this.expandedResult();
    return res ? countWords(res.short) : 0;
  });

  async expandPrompt(): Promise<void> {
    const idea = this.userIdea().trim();
    if (!idea) return;

    this.isExpanding.set(true);
    this.errorMessage.set(null);

    try {
      const response = await this.mossService.expandPrompt(idea, {
        dataset_id: this.datasetId(),
        trigger_word: this.triggerWord(),
        class_noun: this.classNoun(),
        mode: this.trainingMode(),
      });
      this.expandedResult.set(response);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unerwarteter Fehler beim Abruf von /api/rag/expand-prompt';
      this.errorMessage.set(msg);
    } finally {
      this.isExpanding.set(false);
    }
  }

  async copyText(text: string, tier: 'long' | 'medium' | 'short'): Promise<void> {
    if (!text) return;
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(text);
      }
      this.copiedTier.set(tier);
      setTimeout(() => this.copiedTier.set(null), 2000);
    } catch {
      // ignore
    }
  }
}
