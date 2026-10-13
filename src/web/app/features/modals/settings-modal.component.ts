import { ChangeDetectionStrategy, Component, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideX, lucideSave } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { SettingsService } from '../../core/services/settings.service';
import { PipelineSettings } from '../../core/models/settings.model';

@Component({
  selector: 'app-settings-modal',
  imports: [FormsModule, NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideX, lucideSave })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (isOpen()) {
      <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in select-none"
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
            >
              <ng-icon name="lucideX" class="size-4" />
            </button>
          </div>

          <!-- Body Form Fields -->
          <div class="flex flex-col gap-3 text-xs">
            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">Default Frames per Chunk (K):</label>
              <input
                type="number"
                min="1"
                max="50"
                [(ngModel)]="formData.default_k"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">Default Content Rule:</label>
              <select
                [(ngModel)]="formData.default_rule"
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
              <label class="font-medium text-foreground">Sampling FPS during Extraction:</label>
              <input
                type="number"
                min="1.0"
                max="15.0"
                step="0.5"
                [(ngModel)]="formData.sample_fps"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">Survivor Fraction to Classifier (%):</label>
              <input
                type="number"
                min="0.1"
                max="1.0"
                step="0.05"
                [(ngModel)]="formData.survivor_frac"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">Default Trigger Word:</label>
              <input
                type="text"
                placeholder="e.g. restr_elg"
                [(ngModel)]="formData.trigger_word"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
            </div>

            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">Max Export Dimension (px):</label>
              <input
                type="number"
                min="512"
                max="4096"
                step="16"
                [(ngModel)]="formData.max_edge"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
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

  formData: PipelineSettings = {
    default_k: 5,
    default_rule: 'person',
    sample_fps: 4.0,
    survivor_frac: 0.5,
    trigger_word: '',
    max_edge: 2048,
  };

  constructor() {
    this.formData = { ...this.settingsService.settings() };
  }

  async onSave(): Promise<void> {
    await this.settingsService.saveSettings(this.formData);
    this.close.emit();
  }
}
