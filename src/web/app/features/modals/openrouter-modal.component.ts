import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideX, lucideZap, lucideInfo } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ApiService } from '../../core/services/api.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { ChunkService } from '../../core/services/chunk.service';
import { CurateService } from '../../core/services/curate.service';

@Component({
  selector: 'app-openrouter-modal',
  imports: [FormsModule, NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideX, lucideZap, lucideInfo })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (isOpen()) {
      <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in select-none"
        (click)="close.emit()"
      >
        <div
          class="relative w-full max-w-2xl rounded-xl border border-border bg-card p-6 shadow-2xl"
          (click)="$event.stopPropagation()"
        >
          <!-- Header -->
          <div class="flex items-center justify-between border-b border-border pb-3 mb-4">
            <h3 class="font-sans font-bold text-base text-foreground flex items-center gap-2">
              <span class="text-primary">🚀</span>
              <span>VLM Captioning (Gemma 4 31B ➜ DeepSeek ➜ Grok 4.7 Cascade)</span>
            </h3>
            <button
              (click)="close.emit()"
              class="text-muted-foreground hover:text-foreground cursor-pointer"
            >
              <ng-icon name="lucideX" class="size-4" />
            </button>
          </div>

          <!-- Body -->
          <div class="flex flex-col gap-4 text-xs">
            <!-- Cascade Info Box -->
            <div class="flex items-start gap-2.5 rounded-lg border border-primary/20 bg-primary/10 p-3 text-foreground">
              <ng-icon name="lucideInfo" class="size-4 text-primary shrink-0 mt-0.5" />
              <div class="text-[11px] leading-relaxed">
                <strong>Token-Efficient 3-Layer Cascade:</strong> Frames are resized to <strong>max 512×512 px</strong> before sending to OpenRouter (saving ~87% tokens). Uses <strong>Google Gemma 4 31B</strong> by default with high reasoning budget and max 300 tokens. Captions that succeed are kept immediately. <strong>Only refused images cascade</strong> to <strong>DeepSeek V4.1 Flash</strong> (Layer 2), and only images refused by both cascade to <strong>Grok 4.7</strong> (Layer 3).
              </div>
            </div>

            <!-- API Key Input -->
            <div class="flex flex-col gap-1">
              <label class="font-medium text-foreground">OpenRouter API Key:</label>
              <input
                type="password"
                [(ngModel)]="apiKey"
                placeholder="sk-or-v1-..."
                autocomplete="off"
                class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
              />
              <span class="text-[10px] text-muted-foreground">
                Keys can also be configured via <code>OPENROUTER_API_KEY</code> environment variable.
              </span>
            </div>

            <!-- Model & Skillset Selection -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Target Model (Layer 1):</label>
                <select
                  [(ngModel)]="model"
                  class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
                >
                  <option value="google/gemma-4-31b-it">google/gemma-4-31b-it (Default, High Reasoning)</option>
                  <option value="deepseek/deepseek-v4.1-flash">deepseek/deepseek-v4.1-flash (Layer 2 Fallback)</option>
                  <option value="x-ai/grok-4.7">x-ai/grok-4.7 (Layer 3 Fallback, Uncensored)</option>
                  <option value="qwen/qwen-2.5-vl-72b-instruct">qwen/qwen-2.5-vl-72b-instruct</option>
                  <option value="google/gemini-2.0-flash-exp:free">google/gemini-2.0-flash-exp:free</option>
                  <option value="mistralai/pixtral-12b">mistralai/pixtral-12b</option>
                </select>
              </div>

              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Captioning Skillset Mode:</label>
                <select
                  [(ngModel)]="mode"
                  class="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:ring-1 focus:ring-ring"
                >
                  <option value="concept">Concept (Restraint / Bondage taxonomy)</option>
                  <option value="character">Character (Subject decoupling, subtraction principle)</option>
                  <option value="style">Style (Lighting, texture, photographic staging)</option>
                </select>
              </div>
            </div>

            <!-- Trigger Word & Class Noun -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Token 0 Trigger Word:</label>
                <input
                  type="text"
                  [(ngModel)]="triggerWord"
                  placeholder="e.g. restrained_elegance"
                  class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
                />
              </div>

              <div class="flex flex-col gap-1">
                <label class="font-medium text-foreground">Class / Subject Noun:</label>
                <input
                  type="text"
                  [(ngModel)]="classNoun"
                  placeholder="e.g. woman, ropes"
                  class="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground focus:ring-1 focus:ring-ring"
                />
              </div>
            </div>

            <!-- Spec Summary Pills -->
            <div class="flex flex-wrap gap-1.5 pt-1">
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Layer 1: <strong>Gemma 4 31B</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Layer 2: <strong>DeepSeek V4.1 Flash</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Layer 3: <strong>Grok 4.7</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Reasoning: <strong>High</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Max Tokens: <strong>300</strong></span>
              <span class="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">Payload: <strong>≤ 512×512 px</strong></span>
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
              (click)="onStartCaptioning()"
              [disabled]="isSubmitting()"
              class="cursor-pointer gap-1.5 font-bold"
            >
              <ng-icon name="lucideZap" class="size-3.5" />
              <span>{{ isSubmitting() ? 'Starting...' : '🚀 Start Batch Captioning (' + totalFrames() + ' Frames)' }}</span>
            </button>
          </div>
        </div>
      </div>
    }
  `,
})
export class OpenRouterModalComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);
  readonly chunkService = inject(ChunkService);
  private readonly curateService = inject(CurateService);

  readonly isOpen = input<boolean>(false);
  readonly close = output<void>();

  apiKey = '';
  model = 'google/gemma-4-31b-it';
  mode = 'concept';
  triggerWord = 'restrained_elegance';
  classNoun = 'woman, ropes';
  readonly isSubmitting = signal<boolean>(false);

  readonly totalFrames = computed(() => {
    return this.curateService.distribution()?.selected_count || 300;
  });

  ngOnInit(): void {
    if (typeof localStorage !== 'undefined') {
      const savedKey = localStorage.getItem('cp_openrouter_api_key');
      if (savedKey) {
        this.apiKey = savedKey;
      }
    }
  }

  async onStartCaptioning(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video) return;

    try {
      this.isSubmitting.set(true);

      const payload: Record<string, any> = {
        model: this.model || 'google/gemma-4-31b-it',
        fallback_layer2: 'deepseek/deepseek-v4.1-flash',
        fallback_layer3: 'x-ai/grok-4.7',
        // Reasoning shares the token budget with the caption and truncated it; the backend default is off
        reasoning_effort: 'none',
        use_chunk_models: true,
        mode: this.mode || 'concept',
        trigger: this.triggerWord || 'restrained_elegance',
        class_noun: this.classNoun || 'woman, ropes',
      };

      if (this.apiKey && this.apiKey.trim()) {
        payload['api_key'] = this.apiKey.trim();
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem('cp_openrouter_api_key', this.apiKey.trim());
        }
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
      alert(`Captioning error: ${err}`);
    } finally {
      this.isSubmitting.set(false);
    }
  }
}
