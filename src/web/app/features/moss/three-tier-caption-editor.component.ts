import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  model,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideAlertCircle,
  lucideAlertTriangle,
  lucideCheck,
  lucideCheckCircle2,
  lucideCopy,
  lucideQuote,
  lucideSparkles,
  lucideTrash2,
  lucideWand2,
  lucideX,
} from '@ng-icons/lucide';
import {
  TIER_RANGES,
  ThreeTierCaption,
  TierTargetRange,
} from '../../core/models/moss.model';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmTextareaImports } from '@spartan-ng/helm/textarea';

export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).filter((w) => w.length > 0).length;
}

export function extractQuotes(text: string): string[] {
  const matches = text.match(/"([^"]+)"/g);
  if (!matches) return [];
  return matches.map((m) => m.replace(/^"|"$/g, '').trim()).filter((m) => m.length > 0);
}

export function detectTraitLeaks(text: string, traits: readonly string[]): string[] {
  if (!text || traits.length === 0) return [];
  const found: string[] = [];
  for (const trait of traits) {
    const clean = trait.trim();
    if (!clean) continue;
    // Word boundary case-insensitive search
    const escaped = clean.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`\\b${escaped}\\b`, 'i');
    if (regex.test(text) && !found.includes(clean)) {
      found.push(clean);
    }
  }
  return found;
}

export function removeTraitFromText(text: string, trait: string): string {
  if (!text || !trait) return text;
  const escaped = trait.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Match trait with optional leading comma-space
  let result = text.replace(new RegExp(`(?:,\\s*)?\\b${escaped}\\b`, 'gi'), '');
  // Clean up duplicate commas, orphan commas, and double spaces
  result = result
    .replace(/,\s*,+/g, ',')
    .replace(/\s+,/g, ',')
    .replace(/^\s*,\s*/, '')
    .replace(/\s*,\s*$/, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
  return result;
}

@Component({
  selector: 'app-three-tier-caption-editor',
  imports: [FormsModule, NgIcon, HlmBadgeImports, HlmButtonImports, HlmTextareaImports],
  providers: [
    provideIcons({
      lucideAlertTriangle,
      lucideAlertCircle,
      lucideCheckCircle2,
      lucideCheck,
      lucideSparkles,
      lucideQuote,
      lucideWand2,
      lucideTrash2,
      lucideCopy,
      lucideX,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-5 rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm">
      <!-- Header Bar -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div>
          <div class="flex items-center gap-2">
            <h3 class="text-base font-semibold tracking-tight text-foreground">
              Synchronisierter 3-Tier Caption Editor
            </h3>
            <span hlmBadge variant="outline" class="text-xs font-mono">
              MOSS Multi-Tier
            </span>
          </div>
          <p class="text-xs text-muted-foreground mt-0.5">
            Hierarchische Bildbeschreibungen: Long (110-220 W), Medium (40-75 W), Short (10-22 W).
          </p>
        </div>

        <!-- Hierarchy & Leakage Overall Status -->
        <div class="flex items-center gap-2">
          @if (!isHierarchyValid()) {
            <span
              hlmBadge
              variant="destructive"
              class="gap-1 text-xs px-2.5 py-1"
              title="Hierarchie muss eingehalten werden: Long > Medium > Short >= 5"
            >
              <ng-icon name="lucideAlertTriangle" class="size-3.5" />
              <span>Hierarchie ungültig</span>
            </span>
          } @else {
            <span
              class="inline-flex items-center gap-1 rounded-md bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-xs font-medium text-emerald-500"
            >
              <ng-icon name="lucideCheckCircle2" class="size-3.5" />
              <span>Hierarchie OK</span>
            </span>
          }

          @if (allLeakedTraits().length > 0) {
            <span
              class="inline-flex items-center gap-1 rounded-md bg-destructive/15 border border-destructive/30 px-2.5 py-1 text-xs font-medium text-destructive animate-pulse"
            >
              <ng-icon name="lucideAlertCircle" class="size-3.5" />
              <span>{{ allLeakedTraits().length }} Leakage erkannt</span>
            </span>
          }
        </div>
      </div>

      <!-- Warning Banner if Leaked Traits detected -->
      @if (allLeakedTraits().length > 0) {
        <div class="rounded-lg border border-destructive/40 bg-destructive/10 p-3 flex flex-col gap-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2 text-destructive font-semibold text-xs">
              <ng-icon name="lucideAlertCircle" class="size-4 shrink-0" />
              <span>Entkopplungs-Warnung: Folgende gesperrte Traits wurden in den Captions entdeckt:</span>
            </div>
            <button
              type="button"
              hlmButton
              size="sm"
              variant="destructive"
              class="h-6 text-[11px] px-2 gap-1"
              (click)="stripAllLeakedTraits()"
            >
              <ng-icon name="lucideWand2" class="size-3" />
              <span>Alle Leaks bereinigen</span>
            </button>
          </div>

          <!-- Badges for leaked traits - clicking one strips it -->
          <div class="flex flex-wrap gap-1.5 items-center">
            @for (leak of allLeakedTraits(); track leak) {
              <button
                type="button"
                (click)="stripTrait(leak)"
                class="group inline-flex items-center gap-1 rounded-full border border-destructive/50 bg-destructive/20 hover:bg-destructive hover:text-white px-2.5 py-0.5 text-xs font-mono text-destructive transition-colors cursor-pointer"
                [title]="leakTooltip(leak)"
              >
                <span>{{ leak }}</span>
                <ng-icon name="lucideX" class="size-3 group-hover:scale-110 transition-transform" />
              </button>
            }
          </div>
        </div>
      }

      <!-- Hierarchy Violation Explanation Banner -->
      @if (!isHierarchyValid()) {
        <div class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 flex items-center gap-2 text-amber-500 text-xs">
          <ng-icon name="lucideAlertTriangle" class="size-4 shrink-0 text-amber-500" />
          <span>
            <strong>Hierarchie-Regel verletzt:</strong>
            Long ({{ longWordCount() }}) > Medium ({{ mediumWordCount() }}) > Short ({{ shortWordCount() }}) mit Short &ge; 5 Wörtern.
          </span>
        </div>
      }

      <!-- Stacked Auto-Resizing Textareas -->
      <div class="flex flex-col gap-4">
        <!-- 1. LONG CAPTION -->
        <div class="flex flex-col gap-1.5 rounded-lg border border-border/80 bg-canvas/30 p-3.5">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold tracking-wider uppercase text-foreground">
                Tier 1: LONG
              </span>
              <span class="text-[11px] text-muted-foreground font-mono">
                (Ziel: {{ tierRanges.long.min }}–{{ tierRanges.long.max }} Wörter)
              </span>
            </div>

            <!-- Word count badge -->
            <div class="flex items-center gap-2">
              <span
                class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono font-medium border"
                [class.bg-emerald-500/10]="isLongValid()"
                [class.text-emerald-500]="isLongValid()"
                [class.border-emerald-500/30]="isLongValid()"
                [class.bg-amber-500/10]="!isLongValid()"
                [class.text-amber-500]="!isLongValid()"
                [class.border-amber-500/30]="!isLongValid()"
              >
                <span>{{ longWordCount() }} W</span>
                @if (isLongValid()) {
                  <ng-icon name="lucideCheck" class="size-3 stroke-[3]" />
                }
              </span>

              <button
                type="button"
                (click)="copyToClipboard(longCaption(), 'long')"
                class="rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="Tier 1 kopieren"
              >
                <ng-icon [name]="copiedKey() === 'long' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
              </button>
            </div>
          </div>

          <textarea
            #longArea
            hlmTextarea
            rows="4"
            placeholder="Detaillierte visuelle Beschreibung mit Szenerie, Beleuchtung, Subjektmerkmalen, Komposition..."
            [ngModel]="longCaption()"
            (ngModelChange)="onLongChange($event, longArea)"
            class="w-full text-sm font-sans leading-relaxed resize-none transition-all outline-none"
          ></textarea>

          <!-- Inline Highlights / Detected quotes or leaks for Tier 1 -->
          @if (longLeaks().length > 0 || longQuotes().length > 0) {
            <div class="flex flex-wrap gap-2 items-center pt-1 text-[11px]">
              @if (longLeaks().length > 0) {
                <span class="text-destructive font-medium">Leaks in Long:</span>
                @for (leak of longLeaks(); track leak) {
                  <button
                    type="button"
                    (click)="stripTrait(leak)"
                    class="rounded bg-destructive/15 text-destructive px-1.5 py-0.2 font-mono hover:bg-destructive hover:text-white transition-colors"
                  >
                    {{ leak }} &times;
                  </button>
                }
              }

              @if (longQuotes().length > 0) {
                <span class="text-cyan-400 font-medium ml-1 flex items-center gap-1">
                  <ng-icon name="lucideQuote" class="size-2.5" />
                  Transkribierter Text:
                </span>
                @for (q of longQuotes(); track q) {
                  <span class="rounded bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 px-1.5 py-0.2 font-mono">
                    "{{ q }}"
                  </span>
                }
              }
            </div>
          }
        </div>

        <!-- 2. MEDIUM CAPTION -->
        <div class="flex flex-col gap-1.5 rounded-lg border border-border/80 bg-canvas/30 p-3.5">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold tracking-wider uppercase text-foreground">
                Tier 2: MEDIUM
              </span>
              <span class="text-[11px] text-muted-foreground font-mono">
                (Ziel: {{ tierRanges.medium.min }}–{{ tierRanges.medium.max }} Wörter)
              </span>
            </div>

            <!-- Word count badge -->
            <div class="flex items-center gap-2">
              <span
                class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono font-medium border"
                [class.bg-emerald-500/10]="isMediumValid()"
                [class.text-emerald-500]="isMediumValid()"
                [class.border-emerald-500/30]="isMediumValid()"
                [class.bg-amber-500/10]="!isMediumValid()"
                [class.text-amber-500]="!isMediumValid()"
                [class.border-amber-500/30]="!isMediumValid()"
              >
                <span>{{ mediumWordCount() }} W</span>
                @if (isMediumValid()) {
                  <ng-icon name="lucideCheck" class="size-3 stroke-[3]" />
                }
              </span>

              <button
                type="button"
                (click)="copyToClipboard(mediumCaption(), 'medium')"
                class="rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="Tier 2 kopieren"
              >
                <ng-icon [name]="copiedKey() === 'medium' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
              </button>
            </div>
          </div>

          <textarea
            #medArea
            hlmTextarea
            rows="3"
            placeholder="Fokussierte Anker-Prosa mit Kernpose, Hauptobjekten und zentraler Lichtstimmung..."
            [ngModel]="mediumCaption()"
            (ngModelChange)="onMediumChange($event, medArea)"
            class="w-full text-sm font-sans leading-relaxed resize-none transition-all outline-none"
          ></textarea>

          <!-- Inline Highlights / Detected quotes or leaks for Tier 2 -->
          @if (mediumLeaks().length > 0 || mediumQuotes().length > 0) {
            <div class="flex flex-wrap gap-2 items-center pt-1 text-[11px]">
              @if (mediumLeaks().length > 0) {
                <span class="text-destructive font-medium">Leaks in Medium:</span>
                @for (leak of mediumLeaks(); track leak) {
                  <button
                    type="button"
                    (click)="stripTrait(leak)"
                    class="rounded bg-destructive/15 text-destructive px-1.5 py-0.2 font-mono hover:bg-destructive hover:text-white transition-colors"
                  >
                    {{ leak }} &times;
                  </button>
                }
              }

              @if (mediumQuotes().length > 0) {
                <span class="text-cyan-400 font-medium ml-1 flex items-center gap-1">
                  <ng-icon name="lucideQuote" class="size-2.5" />
                  Transkribierter Text:
                </span>
                @for (q of mediumQuotes(); track q) {
                  <span class="rounded bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 px-1.5 py-0.2 font-mono">
                    "{{ q }}"
                  </span>
                }
              }
            </div>
          }
        </div>

        <!-- 3. SHORT CAPTION -->
        <div class="flex flex-col gap-1.5 rounded-lg border border-border/80 bg-canvas/30 p-3.5">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold tracking-wider uppercase text-foreground">
                Tier 3: SHORT
              </span>
              <span class="text-[11px] text-muted-foreground font-mono">
                (Ziel: {{ tierRanges.short.min }}–{{ tierRanges.short.max }} Wörter, mind. 5)
              </span>
            </div>

            <!-- Word count badge -->
            <div class="flex items-center gap-2">
              <span
                class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono font-medium border"
                [class.bg-emerald-500/10]="isShortValid()"
                [class.text-emerald-500]="isShortValid()"
                [class.border-emerald-500/30]="isShortValid()"
                [class.bg-amber-500/10]="!isShortValid()"
                [class.text-amber-500]="!isShortValid()"
                [class.border-amber-500/30]="!isShortValid()"
              >
                <span>{{ shortWordCount() }} W</span>
                @if (isShortValid()) {
                  <ng-icon name="lucideCheck" class="size-3 stroke-[3]" />
                }
              </span>

              <button
                type="button"
                (click)="copyToClipboard(shortCaption(), 'short')"
                class="rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="Tier 3 kopieren"
              >
                <ng-icon [name]="copiedKey() === 'short' ? 'lucideCheck' : 'lucideCopy'" class="size-3.5" />
              </button>
            </div>
          </div>

          <textarea
            #shortArea
            hlmTextarea
            rows="2"
            placeholder="Prägnanter Token- und Hauptsubjekt-Trigger..."
            [ngModel]="shortCaption()"
            (ngModelChange)="onShortChange($event, shortArea)"
            class="w-full text-sm font-sans leading-relaxed resize-none transition-all outline-none"
          ></textarea>

          <!-- Inline Highlights / Detected quotes or leaks for Tier 3 -->
          @if (shortLeaks().length > 0 || shortQuotes().length > 0) {
            <div class="flex flex-wrap gap-2 items-center pt-1 text-[11px]">
              @if (shortLeaks().length > 0) {
                <span class="text-destructive font-medium">Leaks in Short:</span>
                @for (leak of shortLeaks(); track leak) {
                  <button
                    type="button"
                    (click)="stripTrait(leak)"
                    class="rounded bg-destructive/15 text-destructive px-1.5 py-0.2 font-mono hover:bg-destructive hover:text-white transition-colors"
                  >
                    {{ leak }} &times;
                  </button>
                }
              }

              @if (shortQuotes().length > 0) {
                <span class="text-cyan-400 font-medium ml-1 flex items-center gap-1">
                  <ng-icon name="lucideQuote" class="size-2.5" />
                  Transkribierter Text:
                </span>
                @for (q of shortQuotes(); track q) {
                  <span class="rounded bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 px-1.5 py-0.2 font-mono">
                    "{{ q }}"
                  </span>
                }
              }
            </div>
          }
        </div>
      </div>
    </div>
  `,
})
export class ThreeTierCaptionEditorComponent {
  readonly tierRanges = TIER_RANGES;

  // Signal models for 3 tiers
  readonly longCaption = model<string>('');
  readonly mediumCaption = model<string>('');
  readonly shortCaption = model<string>('');

  // Input for locked traits to detect leaks
  readonly lockedTraits = input<string[]>([]);

  // Output event
  readonly captionsChange = output<ThreeTierCaption>();

  // Temporary copy notification key
  readonly copiedKey = signal<string | null>(null);

  // Live word counts
  readonly longWordCount = computed(() => countWords(this.longCaption()));
  readonly mediumWordCount = computed(() => countWords(this.mediumCaption()));
  readonly shortWordCount = computed(() => countWords(this.shortCaption()));

  // Hierarchy rule: Long > Medium > Short >= 5
  readonly isHierarchyValid = computed(() => {
    const l = this.longWordCount();
    const m = this.mediumWordCount();
    const s = this.shortWordCount();
    return s >= 5 && m > s && l > m;
  });

  // Range validation for each individual tier
  readonly isLongValid = computed(() => {
    const w = this.longWordCount();
    return (
      w >= TIER_RANGES.long.min &&
      w <= TIER_RANGES.long.max &&
      this.isHierarchyValid()
    );
  });

  readonly isMediumValid = computed(() => {
    const w = this.mediumWordCount();
    return (
      w >= TIER_RANGES.medium.min &&
      w <= TIER_RANGES.medium.max &&
      this.isHierarchyValid()
    );
  });

  readonly isShortValid = computed(() => {
    const w = this.shortWordCount();
    return (
      w >= TIER_RANGES.short.min &&
      w <= TIER_RANGES.short.max &&
      this.isHierarchyValid()
    );
  });

  // Leaks per tier
  readonly longLeaks = computed(() =>
    detectTraitLeaks(this.longCaption(), this.lockedTraits()),
  );
  readonly mediumLeaks = computed(() =>
    detectTraitLeaks(this.mediumCaption(), this.lockedTraits()),
  );
  readonly shortLeaks = computed(() =>
    detectTraitLeaks(this.shortCaption(), this.lockedTraits()),
  );

  // Verbatim quotes per tier
  readonly longQuotes = computed(() => extractQuotes(this.longCaption()));
  readonly mediumQuotes = computed(() => extractQuotes(this.mediumCaption()));
  readonly shortQuotes = computed(() => extractQuotes(this.shortCaption()));

  // All union of detected leaked traits across all tiers
  readonly allLeakedTraits = computed(() => {
    const set = new Set<string>([
      ...this.longLeaks(),
      ...this.mediumLeaks(),
      ...this.shortLeaks(),
    ]);
    return Array.from(set);
  });

  constructor() {
    effect(() => {
      const caps: ThreeTierCaption = {
        long: this.longCaption(),
        medium: this.mediumCaption(),
        short: this.shortCaption(),
      };
      this.captionsChange.emit(caps);
    });
  }

  onLongChange(val: string, el?: HTMLTextAreaElement): void {
    this.longCaption.set(val);
    this.autoResize(el);
  }

  onMediumChange(val: string, el?: HTMLTextAreaElement): void {
    this.mediumCaption.set(val);
    this.autoResize(el);
  }

  onShortChange(val: string, el?: HTMLTextAreaElement): void {
    this.shortCaption.set(val);
    this.autoResize(el);
  }

  private autoResize(el?: HTMLTextAreaElement): void {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }

  leakTooltip(leak: string): string {
    return `Klicken, um '${leak}' aus allen 3 Tiers zu entfernen`;
  }

  stripTrait(trait: string): void {
    this.longCaption.set(removeTraitFromText(this.longCaption(), trait));
    this.mediumCaption.set(removeTraitFromText(this.mediumCaption(), trait));
    this.shortCaption.set(removeTraitFromText(this.shortCaption(), trait));
  }

  stripAllLeakedTraits(): void {
    const leaks = this.allLeakedTraits();
    for (const leak of leaks) {
      this.stripTrait(leak);
    }
  }

  async copyToClipboard(text: string, key: string): Promise<void> {
    if (!text) return;
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(text);
      }
      this.copiedKey.set(key);
      setTimeout(() => this.copiedKey.set(null), 2000);
    } catch {
      // ignore
    }
  }
}
