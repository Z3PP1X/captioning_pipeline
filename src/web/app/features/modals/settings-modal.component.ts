import { ChangeDetectionStrategy, Component, effect, inject, input, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideX, lucideSave } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { SettingsService } from '../../core/services/settings.service';
import { PipelineSettings } from '../../core/models/settings.model';

@Component({
  selector: 'app-settings-modal',
  imports: [NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideX, lucideSave })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (isOpen()) {
      <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-secondary/80 backdrop-blur-xs p-4 animate-in fade-in select-none"
        (click)="close.emit()"
      >
        <div
          class="relative w-full max-w-lg rounded-xl border border-border bg-card p-6 shadow-2xl"
          (click)="$event.stopPropagation()"
        >
          <!-- Header -->
          <div class="flex items-center justify-between border-b border-border pb-3 mb-4">
            <h3 class="font-sans font-bold text-base text-foreground">
              Pipeline Settings
            </h3>
            <button
              (click)="close.emit()"
              class="text-muted-foreground hover:text-foreground cursor-pointer"
              title="Close settings"
            >
              <ng-icon name="lucideX" class="size-4" />
            </button>
          </div>

          <!-- Body Form Fields -->
          <div class="flex flex-col gap-3 text-xs max-h-[70vh] overflow-y-auto pr-1">
            <div class="flex flex-col gap-1">
              <label for="setting-default-k" class="font-medium text-foreground">Default Frames per Chunk (K):</label>
              <input
                id="setting-default-k"
                type="number"
                min="1"
                max="50"
                [value]="formData().default_k"
                (input)="updateField('default_k', +$any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-default-rule" class="font-medium text-foreground">Default Content Rule:</label>
              <select
                id="setting-default-rule"
                [value]="formData().default_rule"
                (change)="updateField('default_rule', $any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              >
                <option value="person">Person</option>
                <option value="equipment">Equipment</option>
                <option value="both">Both</option>
                <option value="either">Either</option>
                <option value="none">None (Pure Quality)</option>
              </select>
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-sample-fps" class="font-medium text-foreground">Sampling FPS during Extraction:</label>
              <input
                id="setting-sample-fps"
                type="number"
                min="1.0"
                max="15.0"
                step="0.5"
                [value]="formData().sample_fps"
                (input)="updateField('sample_fps', +$any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-survivor-frac" class="font-medium text-foreground">Survivor Fraction to Classifier (0.10 – 1.00):</label>
              <input
                id="setting-survivor-frac"
                type="number"
                min="0.1"
                max="1.0"
                step="0.05"
                [value]="formData().survivor_frac"
                (input)="updateField('survivor_frac', +$any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
              />
              <span class="text-xs text-muted-foreground">Fraction of sampled frames kept before scoring (0.5 = 50%)</span>
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-trigger-word" class="font-medium text-foreground">Default Trigger Word:</label>
              <input
                id="setting-trigger-word"
                type="text"
                placeholder="e.g. restr_elg"
                [value]="formData().trigger_word"
                (input)="updateField('trigger_word', $any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-max-edge" class="font-medium text-foreground">Max Export Dimension (px):</label>
              <input
                id="setting-max-edge"
                type="number"
                min="512"
                max="4096"
                step="16"
                [value]="formData().max_edge"
                (input)="updateField('max_edge', +$any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
              />
            </div>

            <!-- Snap to cuts toggle & Snap window -->
            <div class="flex items-center justify-between p-2 rounded-md border border-border bg-muted/20">
              <div class="flex flex-col">
                <label for="setting-snap-to-cuts" class="font-medium text-foreground cursor-pointer">Snap Chunk Points to Cuts</label>
                <span class="text-xs text-muted-foreground">Magnetically snap chunk In/Out to scene cuts</span>
              </div>
              <input
                id="setting-snap-to-cuts"
                type="checkbox"
                [checked]="formData().snap_to_cuts"
                (change)="updateField('snap_to_cuts', $any($event.target).checked)"
                class="size-4 rounded border-input text-primary focus:ring-ring cursor-pointer"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label for="setting-snap-window" class="font-medium text-foreground">Snap Window Radius (seconds):</label>
              <input
                id="setting-snap-window"
                type="number"
                min="0.5"
                max="5.0"
                step="0.1"
                [value]="formData().snap_window_s"
                (input)="updateField('snap_window_s', +$any($event.target).value)"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring tabular-nums"
              />
            </div>
          </div>

          <!-- Footer Actions -->
          <div class="flex items-center justify-end gap-2 border-t border-border pt-4 mt-5">
            <button
              hlmBtn
              variant="outline"
              size="sm"
              (click)="close.emit()"
              class="cursor-pointer"
            >
              Cancel
            </button>
            <button
              hlmBtn
              variant="default"
              size="sm"
              (click)="onSave()"
              class="cursor-pointer gap-1.5 font-bold"
            >
              <ng-icon name="lucideSave" class="size-3.5" />
              <span>Save Settings</span>
            </button>
          </div>
        </div>
      </div>
    }
  `,
})
export class SettingsModalComponent {
  private readonly settingsService = inject(SettingsService);

  readonly isOpen = input<boolean>(false);
  readonly close = output<void>();

  readonly formData = signal<PipelineSettings>({
    default_k: 5,
    default_rule: 'person',
    sample_fps: 4.0,
    survivor_frac: 0.5,
    trigger_word: '',
    max_edge: 2048,
    snap_to_cuts: true,
    snap_window_s: 1.5,
  });

  constructor() {
    effect(() => {
      if (this.isOpen()) {
        const s = this.settingsService.settings();
        this.formData.set({
          default_k: s.default_k ?? 5,
          default_rule: s.default_rule ?? 'person',
          sample_fps: s.sample_fps ?? 4.0,
          survivor_frac: s.survivor_frac ?? 0.5,
          trigger_word: s.trigger_word ?? '',
          max_edge: s.max_edge ?? 2048,
          snap_to_cuts: s.snap_to_cuts ?? true,
          snap_window_s: s.snap_window_s ?? 1.5,
        });
      }
    });
  }

  updateField<K extends keyof PipelineSettings>(field: K, value: PipelineSettings[K]): void {
    this.formData.update((d) => ({ ...d, [field]: value }));
  }

  async onSave(): Promise<void> {
    await this.settingsService.saveSettings(this.formData());
    this.close.emit();
  }
}
