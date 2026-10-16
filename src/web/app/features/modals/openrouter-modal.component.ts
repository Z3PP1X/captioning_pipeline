import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideZap, lucideInfo, lucideChevronRight, lucideCircleCheck, lucideKeyRound } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { ApiService } from '../../core/services/api.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { ChunkService } from '../../core/services/chunk.service';
import { CurateService } from '../../core/services/curate.service';
import { SettingsService } from '../../core/services/settings.service';
import { registerModal } from '../../core/services/ui-state.service';

@Component({
  selector: 'app-openrouter-modal',
  imports: [NgIcon, HlmButtonImports, HlmDialogImports, HlmNativeSelectImports],
  providers: [provideIcons({ lucideZap, lucideInfo, lucideChevronRight, lucideCircleCheck, lucideKeyRound })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-dialog [state]="isOpen() ? 'open' : 'closed'" (closed)="close.emit()">
      <hlm-dialog-content *hlmDialogPortal="let ctx" class="sm:max-w-2xl gap-0" closeLabel="Close captioning dialog">
          <!-- Header -->
          <hlm-dialog-header class="border-b border-border pb-3 mb-4 pr-8">
            <h3 hlmDialogTitle class="font-sans font-bold text-base text-foreground leading-normal flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
              <ng-icon name="lucideZap" class="size-4 text-primary" aria-hidden="true" />
              <span>VLM captioning cascade:</span>
              <span>Gemma 4 31B</span>
              <ng-icon name="lucideChevronRight" class="size-3.5 text-muted-foreground" aria-hidden="true" />
              <span>DeepSeek</span>
              <ng-icon name="lucideChevronRight" class="size-3.5 text-muted-foreground" aria-hidden="true" />
              <span>Grok 4.7</span>
            </h3>
          </hlm-dialog-header>

          <!-- Body -->
          <div class="flex flex-col gap-4 text-xs">
            <!-- Cascade Info Box -->
            <div class="flex items-start gap-2.5 rounded-lg border border-primary/20 bg-primary/10 p-3 text-foreground">
              <ng-icon name="lucideInfo" class="size-4 text-primary shrink-0 mt-0.5" />
              <div class="text-xs leading-relaxed">
                <strong>Token-Efficient 3-Layer Cascade:</strong> Frames are resized to <strong>max 512×512 px</strong> before sending to OpenRouter (saving ~87% tokens). Uses <strong>Google Gemma 4 31B</strong> by default (reasoning off, max 600 tokens, versioned prompts). Captions that pass the validation gate are kept immediately. <strong>Only refused, cut-off or rejected captions cascade</strong> to <strong>DeepSeek V4.1 Flash</strong> (Layer 2), and only images refused by both cascade to <strong>Grok 4.7</strong> (Layer 3).
              </div>
            </div>

            <!-- API Key: stored on the server only (PUT /api/settings), never in the browser -->
            <div class="flex flex-col gap-1">
              <label for="or-api-key" class="font-medium text-foreground">OpenRouter API key</label>
              @if (keyStored()) {
                <span class="flex items-center gap-1.5 text-xs text-foreground" role="status">
                  <ng-icon name="lucideCircleCheck" class="size-3.5 text-success-strong" aria-hidden="true" />
                  Key stored on server. Enter a new key below only to replace it.
                </span>
              }
              <div class="flex items-center gap-2">
                <input
                  id="or-api-key"
                  type="password"
                  [value]="apiKey()"
                  (input)="apiKey.set($any($event.target).value)"
                  [placeholder]="keyStored() ? 'Replace stored key (sk-or-v1-...)' : 'sk-or-v1-...'"
                  autocomplete="off"
                  class="h-8 flex-1 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
                />
                <button
                  hlmBtn
                  variant="outline"
                  size="sm"
                  (click)="onSaveKey()"
                  [disabled]="!apiKey().trim() || isSavingKey()"
                  class="cursor-pointer gap-1.5"
                >
                  <ng-icon name="lucideKeyRound" class="size-3.5" aria-hidden="true" />
                  <span>{{ keyStored() ? 'Replace key' : 'Save key' }}</span>
                </button>
              </div>
              <span class="text-xs text-muted-foreground">
                The key is saved on the server, not in this browser. It can also come from the
                <code>OPENROUTER_API_KEY</code> environment variable.
              </span>
            </div>

            <!-- Model & Skillset Selection -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="flex flex-col gap-1">
                <label for="caption-model" class="font-medium text-foreground">Target Model (Layer 1):</label>
                <hlm-native-select
                  size="sm"
                  class="w-full"
                  selectId="caption-model"
                  selectClass="text-xs"
                  [value]="model()"
                  (valueChange)="model.set($event ?? model())"
                >
                  @for (m of settingsService.openRouterModels(); track m.id) {
                    <option [value]="m.id">{{ m.name }}</option>
                  }
                </hlm-native-select>
              </div>

              <div class="flex flex-col gap-1">
                <label for="caption-mode" class="font-medium text-foreground">Captioning Skillset Mode:</label>
                <hlm-native-select
                  size="sm"
                  class="w-full"
                  selectId="caption-mode"
                  selectClass="text-xs"
                  [value]="mode()"
                  (valueChange)="mode.set($event ?? mode())"
                >
                  <option value="concept">Concept (Restraint / Bondage taxonomy)</option>
                  <option value="character">Character (Subject decoupling, subtraction principle)</option>
                  <option value="style">Style (Lighting, texture, photographic staging)</option>
                </hlm-native-select>
              </div>
            </div>

            <!-- Trigger Word & Class Noun -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Token 0 Trigger Word:</label>
                <input
                  type="text"
                  [value]="triggerWord()"
                  (input)="triggerWord.set($any($event.target).value)"
                  placeholder="e.g. restrained_elegance"
                  class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
                />
              </div>

              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Class / Subject Noun:</label>
                <input
                  type="text"
                  [value]="classNoun()"
                  (input)="classNoun.set($any($event.target).value)"
                  placeholder="e.g. woman, ropes"
                  class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
                />
              </div>
            </div>

            <!-- Temperature -->
            <div class="flex flex-col gap-1">
              <label for="or-temperature" class="font-medium text-foreground">Temperature:</label>
              <input
                id="or-temperature"
                type="number"
                min="0"
                max="1"
                step="0.05"
                [value]="temperature()"
                (input)="onTemperatureInput($any($event.target).value)"
                class="h-8 w-28 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
              <span class="text-xs text-muted-foreground">
                0.3-0.5 keeps captions consistent across the dataset (default 0.4).
              </span>
            </div>

            <!-- Spec Summary Pills -->
            <div class="flex flex-wrap gap-1.5 pt-1">
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Layer 1: <strong>Gemma 4 31B</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Layer 2: <strong>DeepSeek V4.1 Flash</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Layer 3: <strong>Grok 4.7</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Reasoning: <strong>Off</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Max Tokens: <strong>600</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Temperature: <strong>{{ temperature() }}</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">Payload: <strong>≤ 512×512 px</strong></span>
            </div>
          </div>

          <!-- Footer Actions -->
          <hlm-dialog-footer class="items-center border-t border-border pt-4 mt-5">
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
              (click)="onStartCaptioning()"
              [disabled]="isSubmitting()"
              class="cursor-pointer gap-1.5 font-bold"
            >
              <ng-icon name="lucideZap" class="size-3.5" />
              <span>{{ isSubmitting() ? 'Starting...' : 'Start Batch Captioning (' + totalFrames() + ' Frames)' }}</span>
            </button>
          </hlm-dialog-footer>
      </hlm-dialog-content>
    </hlm-dialog>
  `,
})
export class OpenRouterModalComponent {
  private readonly api = inject(ApiService);
  private readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);
  readonly chunkService = inject(ChunkService);
  private readonly curateService = inject(CurateService);
  readonly settingsService = inject(SettingsService);

  readonly isOpen = input<boolean>(false);
  readonly close = output<void>();

  readonly apiKey = signal<string>('');
  readonly model = signal<string>('google/gemma-4-31b-it');
  readonly mode = signal<string>('concept');
  readonly triggerWord = signal<string>('restrained_elegance');
  readonly classNoun = signal<string>('woman, ropes');
  /** Captions want consistency, not creativity: 0.3-0.5 (backend default 0.4). */
  readonly temperature = signal<number>(0.4);
  readonly isSubmitting = signal<boolean>(false);
  readonly isSavingKey = signal<boolean>(false);
  readonly keyStored = computed(() => Boolean(this.settingsService.settings().openrouter_api_key_set));

  readonly totalFrames = computed(() => {
    return this.curateService.distribution()?.selected_count || 300;
  });

  constructor() {
    registerModal('openrouter', () => this.isOpen());
  }

  async onSaveKey(): Promise<boolean> {
    const key = this.apiKey().trim();
    if (!key) return true;
    this.isSavingKey.set(true);
    try {
      await this.settingsService.saveOpenRouterKey(key);
      this.apiKey.set('');
      this.jobEvents.showToast('API key saved', 'The OpenRouter key is stored on the server.');
      return true;
    } catch (err) {
      this.jobEvents.showToast('API key not saved', err instanceof Error ? err.message : String(err), null, true);
      return false;
    } finally {
      this.isSavingKey.set(false);
    }
  }

  onTemperatureInput(value: string): void {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      this.temperature.set(Math.min(1, Math.max(0, parsed)));
    }
  }

  async onStartCaptioning(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;

    try {
      this.isSubmitting.set(true);

      const payload: Record<string, any> = {
        model: this.model() || 'google/gemma-4-31b-it',
        fallback_layer2: 'deepseek/deepseek-v4.1-flash',
        fallback_layer3: 'x-ai/grok-4.7',
        // Reasoning shares the token budget with the caption and truncated it; the backend default is off
        reasoning_effort: 'none',
        use_chunk_models: true,
        mode: this.mode() || 'concept',
        trigger: this.triggerWord() || 'restrained_elegance',
        class_noun: this.classNoun() || 'woman, ropes',
        temperature: this.temperature(),
      };

      // A typed but unsaved key is stored on the server first; the job reads it from there
      if (this.apiKey().trim() && !(await this.onSaveKey())) {
        return;
      }

      await this.api.post(`/api/videos/${video.id}/caption-openrouter`, payload);

      this.jobEvents.showToast(
        'Captioning Started',
        `Batch cascade captioning job dispatched for ${video.name}!`,
        video.id
      );
      this.close.emit();
    } catch (err) {
      console.error('Failed to start batch captioning:', err);
      this.jobEvents.showToast('Captioning Error', `Failed to start batch captioning: ${err}`, null, true);
    } finally {
      this.isSubmitting.set(false);
    }
  }
}
