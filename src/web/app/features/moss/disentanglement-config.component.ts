import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  model,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideLayers,
  lucidePackage,
  lucidePalette,
  lucideSparkles,
  lucideTrash2,
  lucideUser,
  lucideX,
} from '@ng-icons/lucide';
import {
  DisentanglementConfig,
  PipelineType,
  STANDARD_STYLE_BLACKLIST,
  TRAINING_MODES,
  TrainingMode,
} from '../../core/models/moss.model';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmInputImports } from '@spartan-ng/helm/input';

@Component({
  selector: 'app-disentanglement-config',
  imports: [FormsModule, NgIcon, HlmBadgeImports, HlmButtonImports, HlmInputImports],
  providers: [
    provideIcons({
      lucideUser,
      lucidePalette,
      lucidePackage,
      lucideCheck,
      lucideX,
      lucideSparkles,
      lucideLayers,
      lucideTrash2,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-6 rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm">
      <!-- Header & Pipeline Selector Toggle -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/60 pb-4">
        <div>
          <div class="flex items-center gap-2">
            <h3 class="text-base font-semibold tracking-tight text-foreground">
              MOSS-Image Disentanglement Configurator
            </h3>
            <span hlmBadge variant="outline" class="text-xs uppercase tracking-wider font-mono">
              Pipeline Modus
            </span>
          </div>
          <p class="text-xs text-muted-foreground mt-0.5">
            Konfiguration von Entkopplung, locked traits und multi-tier Captions.
          </p>
        </div>

        <!-- Pipeline Toggle Switch: Krea 2 vs MOSS-Image -->
        <div
          role="radiogroup"
          aria-label="Pipeline Modus"
          class="inline-flex items-center rounded-lg border border-border bg-canvas/80 p-1 text-xs font-medium self-start sm:self-auto"
        >
          <button
            type="button"
            role="radio"
            [attr.aria-checked]="pipeline() === 'krea2'"
            (click)="setPipeline('krea2')"
            class="flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all outline-none"
            [class.bg-card]="pipeline() === 'krea2'"
            [class.text-foreground]="pipeline() === 'krea2'"
            [class.shadow-xs]="pipeline() === 'krea2'"
            [class.text-muted-foreground]="pipeline() !== 'krea2'"
          >
            <span>Krea 2</span>
            <span class="text-[10px] text-muted-foreground/80 font-mono">(1 Caption)</span>
          </button>

          <button
            type="button"
            role="radio"
            [attr.aria-checked]="pipeline() === 'moss'"
            (click)="setPipeline('moss')"
            class="flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all outline-none"
            [class.bg-primary]="pipeline() === 'moss'"
            [class.text-primary-foreground]="pipeline() === 'moss'"
            [class.shadow-xs]="pipeline() === 'moss'"
            [class.text-muted-foreground]="pipeline() !== 'moss'"
          >
            <ng-icon name="lucideSparkles" class="size-3.5" />
            <span class="font-semibold">MOSS-Image</span>
            <span
              class="text-[10px] font-mono"
              [class.text-primary-foreground/90]="pipeline() === 'moss'"
              [class.text-muted-foreground/80]="pipeline() !== 'moss'"
            >
              (3-Tier + Geometrie)
            </span>
          </button>
        </div>
      </div>

      <!-- Training Mode Selection Cards -->
      <div class="flex flex-col gap-2.5">
        <label class="text-xs font-semibold tracking-wide text-foreground uppercase">
          Trainings-Modus
        </label>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          @for (item of trainingModes; track item.mode) {
            <button
              type="button"
              (click)="setTrainingMode(item.mode)"
              class="group relative flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition-all outline-none"
              [class.border-primary]="trainingMode() === item.mode"
              [class.bg-primary/5]="trainingMode() === item.mode"
              [class.ring-1]="trainingMode() === item.mode"
              [class.ring-primary]="trainingMode() === item.mode"
              [class.border-border]="trainingMode() !== item.mode"
              [class.bg-card]="trainingMode() !== item.mode"
              [class.hover:border-foreground/30]="trainingMode() !== item.mode"
            >
              <div class="flex w-full items-center justify-between">
                <div class="flex items-center gap-2">
                  <div
                    class="flex size-7 items-center justify-center rounded-md transition-colors"
                    [class.bg-primary]="trainingMode() === item.mode"
                    [class.text-primary-foreground]="trainingMode() === item.mode"
                    [class.bg-muted]="trainingMode() !== item.mode"
                    [class.text-muted-foreground]="trainingMode() !== item.mode"
                  >
                    <ng-icon [name]="item.icon" class="size-4" />
                  </div>
                  <span class="text-sm font-semibold text-foreground">
                    {{ item.label }}
                  </span>
                </div>

                @if (trainingMode() === item.mode) {
                  <span class="flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <ng-icon name="lucideCheck" class="size-3 stroke-[3]" />
                  </span>
                }
              </div>

              <p class="text-xs text-muted-foreground leading-relaxed">
                {{ item.description }}
              </p>

              <span
                hlmBadge
                variant="secondary"
                class="mt-auto text-[10px] py-0 px-1.5"
                [class.bg-primary/10]="trainingMode() === item.mode"
                [class.text-primary]="trainingMode() === item.mode"
              >
                {{ item.badge }}
              </span>
            </button>
          }
        </div>
      </div>

      <!-- Core Class Noun and Trigger Phrase Inputs -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <label for="class-noun-input" class="text-xs font-semibold text-foreground">
            Class Noun (Klassen-Nomen)
          </label>
          <input
            id="class-noun-input"
            hlmInput
            type="text"
            placeholder="z.B. woman, sports car, cyberpunk sword..."
            [ngModel]="classNoun()"
            (ngModelChange)="onClassNounChange($event)"
            class="w-full text-sm font-mono"
          />
          <p class="text-[11px] text-muted-foreground">
            Das generische Nomen der Zielklasse für Entkopplung und Klassen-Prior.
          </p>
        </div>

        <div class="flex flex-col gap-1.5">
          <label for="trigger-phrase-input" class="text-xs font-semibold text-foreground">
            Trigger Phrase (Auslöser-Token)
          </label>
          <input
            id="trigger-phrase-input"
            hlmInput
            type="text"
            placeholder="z.B. ohwx_subject, sks style..."
            [ngModel]="triggerPhrase()"
            (ngModelChange)="onTriggerPhraseChange($event)"
            class="w-full text-sm font-mono"
          />
          <p class="text-[11px] text-muted-foreground">
            Eindeutiges Token oder Phrase für das Training.
          </p>
        </div>
      </div>

      <!-- Interactive Locked-Traits Chip Input -->
      <div class="flex flex-col gap-2.5 border-t border-border/60 pt-4">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div class="flex items-center gap-2">
              <label class="text-xs font-semibold text-foreground uppercase tracking-wide">
                Locked-Traits (Entkopplungs-Blacklist)
              </label>
              <span class="rounded-full bg-muted px-2 py-0.5 text-[11px] font-mono font-medium text-muted-foreground">
                {{ lockedTraits().length }} aktiv
              </span>
            </div>
            <p class="text-xs text-muted-foreground mt-0.5">
              Begriffe, die niemals in den Bildbeschreibungen auftauchen dürfen, um Stil- oder Attribut-Bleeding zu verhindern.
            </p>
          </div>

          <!-- Quick action buttons -->
          <div class="flex items-center gap-2 self-start sm:self-auto">
            <button
              type="button"
              hlmButton
              variant="outline"
              size="sm"
              class="h-7 text-xs gap-1.5 border-primary/40 text-primary hover:bg-primary/10"
              (click)="loadStandardStyleBlacklist()"
            >
              <ng-icon name="lucideSparkles" class="size-3.5" />
              <span>Standard-Stil-Blacklist laden</span>
            </button>

            @if (lockedTraits().length > 0) {
              <button
                type="button"
                hlmButton
                variant="ghost"
                size="sm"
                class="h-7 text-xs gap-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                (click)="clearAllTraits()"
                title="Alle Traits leeren"
              >
                <ng-icon name="lucideTrash2" class="size-3" />
                <span>Leeren</span>
              </button>
            }
          </div>
        </div>

        <!-- Add trait input row -->
        <div class="flex gap-2">
          <input
            #traitInput
            hlmInput
            type="text"
            placeholder="Neues Locked-Trait eingeben und Enter drücken..."
            [ngModel]="newTraitText()"
            (ngModelChange)="newTraitText.set($event)"
            (keydown.enter)="addTraitFromInput($event)"
            class="flex-1 text-sm font-mono"
          />
          <button
            type="button"
            hlmButton
            size="sm"
            variant="secondary"
            class="px-3"
            (click)="addTraitFromInput()"
            [disabled]="!newTraitText().trim()"
          >
            Hinzufügen
          </button>
        </div>

        <!-- Chips Badges Container -->
        <div class="flex flex-wrap gap-1.5 min-h-[38px] p-2 rounded-lg border border-dashed border-border bg-canvas/40">
          @if (lockedTraits().length === 0) {
            <span class="text-xs text-muted-foreground/70 italic self-center px-1">
              Keine Locked-Traits definiert. Trage Begriffe oben ein oder lade die Standard-Blacklist.
            </span>
          } @else {
            @for (trait of lockedTraits(); track trait; let idx = $index) {
              <span
                class="inline-flex items-center gap-1 rounded-md border border-border/80 bg-card px-2.5 py-1 text-xs font-mono text-foreground shadow-2xs hover:border-primary/50 transition-colors"
              >
                <span>{{ trait }}</span>
                <button
                  type="button"
                  (click)="removeTrait(idx)"
                  class="ml-0.5 rounded p-0.5 text-muted-foreground hover:bg-destructive/15 hover:text-destructive transition-colors outline-none"
                  [attr.aria-label]="'Entferne ' + trait"
                >
                  <ng-icon name="lucideX" class="size-3" />
                </button>
              </span>
            }
          }
        </div>
      </div>
    </div>
  `,
})
export class DisentanglementConfigComponent {
  readonly trainingModes = TRAINING_MODES;

  // Angular signal models for 2-way or 1-way binding
  readonly pipeline = model<PipelineType>('moss');
  readonly trainingMode = model<TrainingMode>('CHARACTER');
  readonly lockedTraits = model<string[]>([]);
  readonly classNoun = model<string>('woman');
  readonly triggerPhrase = model<string>('ohwx_subject');

  // Input for adding a new trait
  readonly newTraitText = signal<string>('');

  // Output event for parent components
  readonly configChange = output<DisentanglementConfig>();

  readonly currentConfig = computed<DisentanglementConfig>(() => ({
    pipeline: this.pipeline(),
    trainingMode: this.trainingMode(),
    lockedTraits: this.lockedTraits(),
    classNoun: this.classNoun(),
    triggerPhrase: this.triggerPhrase(),
  }));

  constructor() {
    effect(() => {
      const cfg = this.currentConfig();
      this.configChange.emit(cfg);
    });
  }

  setPipeline(type: PipelineType): void {
    this.pipeline.set(type);
  }

  setTrainingMode(mode: TrainingMode): void {
    this.trainingMode.set(mode);
  }

  onClassNounChange(val: string): void {
    this.classNoun.set(val);
  }

  onTriggerPhraseChange(val: string): void {
    this.triggerPhrase.set(val);
  }

  addTraitFromInput(event?: Event): void {
    if (event) {
      event.preventDefault();
    }
    const raw = this.newTraitText().trim();
    if (!raw) return;

    this.addTrait(raw);
    this.newTraitText.set('');
  }

  addTrait(trait: string): void {
    const clean = trait.trim().toLowerCase();
    if (!clean) return;

    const current = this.lockedTraits();
    const alreadyExists = current.some((t) => t.toLowerCase() === clean);
    if (!alreadyExists) {
      this.lockedTraits.set([...current, clean]);
    }
  }

  removeTrait(index: number): void {
    const current = [...this.lockedTraits()];
    if (index >= 0 && index < current.length) {
      current.splice(index, 1);
      this.lockedTraits.set(current);
    }
  }

  clearAllTraits(): void {
    this.lockedTraits.set([]);
  }

  loadStandardStyleBlacklist(): void {
    const current = new Set(this.lockedTraits().map((t) => t.toLowerCase()));
    for (const item of STANDARD_STYLE_BLACKLIST) {
      current.add(item.toLowerCase());
    }
    this.lockedTraits.set(Array.from(current));
  }
}
