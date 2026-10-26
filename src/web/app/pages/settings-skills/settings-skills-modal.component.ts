import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  OnInit,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  OpenRouterTestResponse,
  StudioSkill,
  TriggerPosition,
} from '../../models/studio.models';
import { StudioApiService } from '../../services/studio-api.service';

@Component({
  selector: 'app-settings-skills-modal',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (isOpen()) {
      <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-xs p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="studio-settings-title"
          class="w-full max-w-5xl max-h-[90vh] rounded-2xl border border-slate-700 bg-slate-900 text-slate-100 shadow-2xl flex flex-col overflow-hidden"
        >
          <!-- Modal Header & Tab Switcher -->
          <div class="flex flex-wrap items-center justify-between gap-3 px-6 py-4 border-b border-slate-800 bg-slate-950">
            <div class="flex flex-wrap items-center gap-4">
              <h2 id="studio-settings-title" class="text-base font-bold uppercase tracking-wider text-orange-300">
                System Settings & Krea 2 Master Skill Manager
              </h2>
              <div class="flex items-center gap-1 bg-slate-900 p-1 rounded-lg border border-slate-800">
                <button
                  type="button"
                  (click)="activeModalTab.set('settings')"
                  class="min-h-8 min-w-8 px-3 py-1.5 rounded text-xs font-semibold cursor-pointer transition"
                  [class.bg-orange-700]="activeModalTab() === 'settings'"
                  [class.text-white]="activeModalTab() === 'settings'"
                  [class.text-slate-300]="activeModalTab() !== 'settings'"
                >
                  1. OpenRouter Cascade & Rejection-Guard
                </button>
                <button
                  type="button"
                  (click)="activeModalTab.set('skills')"
                  class="min-h-8 min-w-8 px-3 py-1.5 rounded text-xs font-semibold cursor-pointer transition"
                  [class.bg-orange-700]="activeModalTab() === 'skills'"
                  [class.text-white]="activeModalTab() === 'skills'"
                  [class.text-slate-300]="activeModalTab() !== 'skills'"
                >
                  2. Versionable Krea 2 Master Skills ({{ api.skills().length }})
                </button>
              </div>
            </div>

            <button
              type="button"
              (click)="closeModal.emit()"
              class="min-h-8 min-w-8 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 cursor-pointer"
            >
              ✕ Close (Esc)
            </button>
          </div>

          <!-- Modal Body -->
          <div class="flex-1 overflow-y-auto p-6">
            @if (activeModalTab() === 'settings') {
              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Left Column: OpenRouter API Key & 3-Model Cascade -->
                <div class="flex flex-col gap-4">
                  <div class="p-4 rounded-xl border border-slate-800 bg-slate-950 flex flex-col gap-3">
                    <div class="flex items-center justify-between">
                      <label for="openrouter-api-key-input" class="text-xs font-bold uppercase tracking-wider text-sky-300">
                        OpenRouter API Key & Connectivity
                      </label>
                      <span
                        class="px-2 py-0.5 rounded text-xs font-mono"
                        [class.bg-emerald-950]="api.settings().openrouter_api_key_set"
                        [class.text-emerald-300]="api.settings().openrouter_api_key_set"
                        [class.bg-amber-950]="!api.settings().openrouter_api_key_set"
                        [class.text-amber-300]="!api.settings().openrouter_api_key_set"
                      >
                        {{
                          api.settings().openrouter_api_key_set
                            ? 'Stored (' + (api.settings().openrouter_api_key_masked || 'sk-or-***') + ')'
                            : 'Not Configured'
                        }}
                      </span>
                    </div>

                    <div class="flex gap-2">
                      <input
                        id="openrouter-api-key-input"
                        type="password"
                        aria-label="OpenRouter API Key"
                        [ngModel]="apiKeyInput()"
                        (ngModelChange)="apiKeyInput.set($event)"
                        placeholder="Leave blank to keep existing key, or paste sk-or-v1-..."
                        class="flex-1 min-h-8 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono text-white"
                      />
                      <button
                        type="button"
                        [disabled]="isTestingKey()"
                        (click)="testOpenRouterKey()"
                        class="min-h-8 min-w-8 px-3 py-2 rounded-lg bg-sky-700 hover:bg-sky-600 text-white text-xs font-bold cursor-pointer disabled:opacity-50"
                      >
                        {{ isTestingKey() ? 'Testing...' : 'Test Key' }}
                      </button>
                    </div>

                    @if (keyTestResult(); as kt) {
                      <div
                        class="p-2.5 rounded-lg text-xs font-mono border"
                        [class.bg-emerald-950]="kt.valid"
                        [class.border-emerald-500/40]="kt.valid"
                        [class.text-emerald-300]="kt.valid"
                        [class.bg-rose-950]="!kt.valid"
                        [class.border-rose-500/40]="!kt.valid"
                        [class.text-rose-300]="!kt.valid"
                      >
                        @if (kt.valid) {
                          ✓ Connected ({{ kt.label }}) — Usage: \${{ (kt.usage_usd ?? 0).toFixed(4) }}
                        } @else {
                          ✕ Connection failed: {{ kt.error || 'Invalid key' }}
                        }
                      </div>
                    }
                  </div>

                  <!-- 3-Tier VLM Model Cascade Placeholders -->
                  <div class="p-4 rounded-xl border border-slate-800 bg-slate-950 flex flex-col gap-3">
                    <div class="text-xs font-bold uppercase tracking-wider text-indigo-300">
                      3-Tier OpenRouter Model Cascade
                    </div>

                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300" for="set-primary-model">
                        Layer 1: Primary Model (High-Speed Vision Default)
                      </label>
                      <input
                        id="set-primary-model"
                        type="text"
                        [ngModel]="primaryModel()"
                        (ngModelChange)="primaryModel.set($event)"
                        placeholder="google/gemma-4-31b-it"
                        class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-900 border border-emerald-500/40 text-xs font-mono text-emerald-300"
                      />
                    </div>

                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300" for="set-fb1-model">
                        Layer 2: Fallback Model 1 (Triggered on Refusal / Truncation)
                      </label>
                      <input
                        id="set-fb1-model"
                        type="text"
                        [ngModel]="fallbackModel1()"
                        (ngModelChange)="fallbackModel1.set($event)"
                        placeholder="deepseek/deepseek-v4.1-flash"
                        class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-900 border border-amber-500/40 text-xs font-mono text-amber-300"
                      />
                    </div>

                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300" for="set-fb2-model">
                        Layer 3: Fallback Model 2 (Uncensored / Final Escalation)
                      </label>
                      <input
                        id="set-fb2-model"
                        type="text"
                        [ngModel]="fallbackModel2()"
                        (ngModelChange)="fallbackModel2.set($event)"
                        placeholder="x-ai/grok-4.7"
                        class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-900 border border-purple-500/40 text-xs font-mono text-purple-300"
                      />
                    </div>
                  </div>
                </div>

                <!-- Right Column: Sampling Sliders, Default Trigger & Custom Rejection Regexes -->
                <div class="flex flex-col gap-4">
                  <div class="p-4 rounded-xl border border-slate-800 bg-slate-950 flex flex-col gap-3">
                    <div class="text-xs font-bold uppercase tracking-wider text-orange-300">
                      Global Sampling & Triggerword Defaults
                    </div>

                    <div class="flex flex-col gap-1">
                      <div class="flex justify-between text-xs">
                        <label for="set-max-tokens" class="text-slate-300">Max Completion Tokens (64..4096)</label>
                        <span class="font-mono text-orange-300 font-bold">{{ maxTokens() }} tokens</span>
                      </div>
                      <input
                        id="set-max-tokens"
                        type="range"
                        aria-label="Max Completion Tokens"
                        min="64"
                        max="4096"
                        step="32"
                        [ngModel]="maxTokens()"
                        (ngModelChange)="maxTokens.set(+$event)"
                        class="accent-orange-500 cursor-pointer"
                      />
                    </div>

                    <div class="flex flex-col gap-1">
                      <div class="flex justify-between text-xs">
                        <label for="set-temperature" class="text-slate-300">Sampling Temperature (0.0..2.0)</label>
                        <span class="font-mono text-orange-300 font-bold">{{ temperature().toFixed(2) }}</span>
                      </div>
                      <input
                        id="set-temperature"
                        type="range"
                        aria-label="Sampling Temperature"
                        min="0.0"
                        max="2.0"
                        step="0.05"
                        [ngModel]="temperature()"
                        (ngModelChange)="temperature.set(+$event)"
                        class="accent-orange-500 cursor-pointer"
                      />
                    </div>

                    <div class="grid grid-cols-2 gap-3 pt-1">
                      <div class="flex flex-col gap-1">
                        <label class="text-xs text-slate-300" for="set-default-trig">Default Triggerword</label>
                        <input
                          id="set-default-trig"
                          type="text"
                          [ngModel]="defaultTriggerWord()"
                          (ngModelChange)="defaultTriggerWord.set($event)"
                          class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono text-sky-300"
                        />
                      </div>

                      <div class="flex flex-col gap-1">
                        <label class="text-xs text-slate-300" for="set-default-pos">Default Placement</label>
                        <select
                          id="set-default-pos"
                          [ngModel]="defaultTriggerPos()"
                          (ngModelChange)="defaultTriggerPos.set($event)"
                          class="min-h-8 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono text-white"
                        >
                          <option value="prefix">prefix (Token 0)</option>
                          <option value="suffix">suffix</option>
                          <option value="natural_integration">natural_integration</option>
                        </select>
                      </div>
                    </div>
                  </div>

                  <!-- Custom Rejection-Guard Regex Rules -->
                  <div class="p-4 rounded-xl border border-slate-800 bg-slate-950 flex flex-col gap-2">
                    <label for="set-rejection-regexes" class="text-xs font-bold uppercase tracking-wider text-rose-300">
                      Custom Rejection-Guard Regex Patterns (One per line)
                    </label>
                    <p class="text-xs text-slate-300">
                      Appended to built-in refusal, preamble, and truncation guards during cascade evaluation
                    </p>
                    <textarea
                      id="set-rejection-regexes"
                      aria-label="Custom Rejection-Guard Regex Patterns"
                      rows="4"
                      [ngModel]="customRejectionRegexesText()"
                      (ngModelChange)="customRejectionRegexesText.set($event)"
                      placeholder="e.g.\bas an ai model\b&#10;\bcannot assist with\b"
                      class="w-full p-2.5 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono text-rose-200"
                    ></textarea>
                  </div>

                  <button
                    type="button"
                    (click)="saveGlobalSettings()"
                    class="w-full min-h-8 min-w-8 px-4 py-2.5 rounded-xl bg-emerald-700 hover:bg-emerald-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer"
                  >
                    💾 Save System Settings
                  </button>
                </div>
              </div>
            } @else {
              <!-- Tab 2: Versionable Master Skill Editor (Krea 2 Character, Concept & Style) -->
              <div class="grid grid-cols-1 md:grid-cols-12 gap-5">
                <!-- Skill List Sidebar -->
                <div class="md:col-span-4 flex flex-col gap-2">
                  <div class="text-xs font-bold uppercase tracking-wider text-slate-300 mb-1">
                    Master Skills (SQLite Versioned)
                  </div>
                  @for (sk of api.skills(); track sk.id) {
                    <button
                      type="button"
                      (click)="selectSkillForEdit(sk)"
                      class="min-h-8 min-w-8 p-3 rounded-xl border text-left transition cursor-pointer flex flex-col gap-1"
                      [class.border-orange-500]="editingSkillSlug() === sk.slug && editingSkillVersion() === sk.version"
                      [class.bg-slate-800]="editingSkillSlug() === sk.slug && editingSkillVersion() === sk.version"
                      [class.border-slate-800]="editingSkillSlug() !== sk.slug || editingSkillVersion() !== sk.version"
                      [class.bg-slate-950]="editingSkillSlug() !== sk.slug || editingSkillVersion() !== sk.version"
                    >
                      <div class="flex items-center justify-between">
                        <span class="text-xs font-bold text-white">{{ sk.name }}</span>
                        <span class="px-1.5 py-0.5 rounded bg-orange-950 text-orange-300 font-mono text-xs">
                          v{{ sk.version }}
                        </span>
                      </div>
                      <div class="flex items-center justify-between text-xs font-mono text-slate-300">
                        <span>{{ sk.training_type }}</span>
                        <span>{{ sk.min_words }}–{{ sk.max_words }} words</span>
                      </div>
                    </button>
                  }
                </div>

                <!-- Skill Template Editor -->
                <div class="md:col-span-8 flex flex-col gap-3 bg-slate-950 p-4 rounded-xl border border-slate-800">
                  <div class="grid grid-cols-3 gap-3">
                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300 font-semibold" for="skill-name-in">Skill Name</label>
                      <input
                        id="skill-name-in"
                        type="text"
                        [ngModel]="editingSkillName()"
                        (ngModelChange)="editingSkillName.set($event)"
                        class="min-h-8 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs text-white"
                      />
                    </div>

                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300 font-semibold" for="skill-slug-in">Skill Slug</label>
                      <input
                        id="skill-slug-in"
                        type="text"
                        [ngModel]="editingSkillSlug()"
                        (ngModelChange)="editingSkillSlug.set($event)"
                        class="min-h-8 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-sky-300"
                      />
                    </div>

                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300 font-semibold" for="skill-type-in">Training Mode</label>
                      <select
                        id="skill-type-in"
                        [ngModel]="editingTrainingType()"
                        (ngModelChange)="editingTrainingType.set($event)"
                        class="min-h-8 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs text-white"
                      >
                        <option value="character">character (Identity Decoupled)</option>
                        <option value="concept">concept (Pose / Restraint / Optics)</option>
                        <option value="style">style (Content Inversion)</option>
                      </select>
                    </div>
                  </div>

                  <div class="grid grid-cols-2 gap-3">
                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300" for="skill-min-w">Target Min Words</label>
                      <input
                        id="skill-min-w"
                        type="number"
                        [ngModel]="editingMinWords()"
                        (ngModelChange)="editingMinWords.set(+$event)"
                        class="min-h-8 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-white"
                      />
                    </div>
                    <div class="flex flex-col gap-1">
                      <label class="text-xs text-slate-300" for="skill-max-w">Target Max Words</label>
                      <input
                        id="skill-max-w"
                        type="number"
                        [ngModel]="editingMaxWords()"
                        (ngModelChange)="editingMaxWords.set(+$event)"
                        class="min-h-8 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-white"
                      />
                    </div>
                  </div>

                  <div class="flex flex-col gap-1">
                    <label class="text-xs text-slate-300 font-semibold" for="skill-sys-prompt">
                      System Prompt Template (Supports {{ '{' }}trigger{{ '}' }}, {{ '{' }}class_noun{{ '}' }}, {{ '{' }}tags{{ '}' }})
                    </label>
                    <textarea
                      id="skill-sys-prompt"
                      rows="6"
                      [ngModel]="editingSystemPrompt()"
                      (ngModelChange)="editingSystemPrompt.set($event)"
                      class="w-full p-2.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-slate-200 leading-relaxed"
                    ></textarea>
                  </div>

                  <div class="flex flex-col gap-1">
                    <label class="text-xs text-slate-300 font-semibold" for="skill-usr-prompt">
                      User Prompt Template
                    </label>
                    <textarea
                      id="skill-usr-prompt"
                      rows="3"
                      [ngModel]="editingUserPrompt()"
                      (ngModelChange)="editingUserPrompt.set($event)"
                      class="w-full p-2.5 rounded bg-slate-900 border border-slate-700 text-xs font-mono text-slate-200 leading-relaxed"
                    ></textarea>
                  </div>

                  <div class="flex justify-end pt-2">
                    <button
                      type="button"
                      (click)="saveNewSkillVersion()"
                      class="min-h-8 min-w-8 px-4 py-2 rounded-lg bg-orange-700 hover:bg-orange-600 text-white font-bold text-xs uppercase tracking-wider shadow cursor-pointer"
                    >
                      ✨ Save as New Skill Version (v{{ editingSkillVersion() + 1 }})
                    </button>
                  </div>
                </div>
              </div>
            }
          </div>
        </div>
      </div>
    }
  `,
})
export class SettingsSkillsModalComponent implements OnInit {
  readonly api = inject(StudioApiService);

  readonly isOpen = input<boolean>(false);
  readonly closeModal = output<void>();

  readonly activeModalTab = signal<'settings' | 'skills'>('settings');

  @HostListener('window:keydown.escape')
  onEscapeKey(): void {
    if (this.isOpen()) {
      this.closeModal.emit();
    }
  }

  // Settings Form Signals
  readonly apiKeyInput = signal<string>('');
  readonly primaryModel = signal<string>('google/gemma-4-31b-it');
  readonly fallbackModel1 = signal<string>('deepseek/deepseek-v4.1-flash');
  readonly fallbackModel2 = signal<string>('x-ai/grok-4.7');
  readonly maxTokens = signal<number>(600);
  readonly temperature = signal<number>(0.4);
  readonly defaultTriggerWord = signal<string>('ohwx_subject');
  readonly defaultTriggerPos = signal<TriggerPosition>('prefix');
  readonly customRejectionRegexesText = signal<string>('');

  readonly isTestingKey = signal<boolean>(false);
  readonly keyTestResult = signal<OpenRouterTestResponse | null>(null);

  // Skill Editor Signals
  readonly editingSkillSlug = signal<string>('krea2_character');
  readonly editingSkillName = signal<string>('Krea 2 Character Training');
  readonly editingSkillVersion = signal<number>(1);
  readonly editingTrainingType = signal<'character' | 'concept' | 'style' | 'general'>('character');
  readonly editingMinWords = signal<number>(80);
  readonly editingMaxWords = signal<number>(140);
  readonly editingSystemPrompt = signal<string>('');
  readonly editingUserPrompt = signal<string>('');

  constructor() {
    effect(() => {
      const s = this.api.settings();
      if (s.primary_model) this.primaryModel.set(s.primary_model);
      if (s.fallback_model_1) this.fallbackModel1.set(s.fallback_model_1);
      if (s.fallback_model_2) this.fallbackModel2.set(s.fallback_model_2);
      if (typeof s.max_tokens === 'number') this.maxTokens.set(s.max_tokens);
      if (typeof s.temperature === 'number') this.temperature.set(s.temperature);
      if (s.trigger_word) this.defaultTriggerWord.set(s.trigger_word);
      if (s.trigger_position) this.defaultTriggerPos.set(s.trigger_position);
      if (Array.isArray(s.custom_rejection_regexes)) {
        this.customRejectionRegexesText.set(s.custom_rejection_regexes.join('\n'));
      }
    });
  }

  async ngOnInit(): Promise<void> {
    await this.api.loadSettings();
    const skills = await this.api.loadSkills();
    if (skills.length > 0) {
      this.selectSkillForEdit(skills[0]);
    }
  }

  selectSkillForEdit(sk: StudioSkill): void {
    this.editingSkillSlug.set(sk.slug);
    this.editingSkillName.set(sk.name);
    this.editingSkillVersion.set(sk.version);
    this.editingTrainingType.set(sk.training_type);
    this.editingMinWords.set(sk.min_words);
    this.editingMaxWords.set(sk.max_words);
    this.editingSystemPrompt.set(sk.system_prompt_template);
    this.editingUserPrompt.set(sk.user_prompt_template);
  }

  async testOpenRouterKey(): Promise<void> {
    this.isTestingKey.set(true);
    try {
      const res = await this.api.testOpenRouterConnection(this.apiKeyInput().trim() || undefined);
      this.keyTestResult.set(res);
    } finally {
      this.isTestingKey.set(false);
    }
  }

  async saveGlobalSettings(): Promise<void> {
    const customRegexes = this.customRejectionRegexesText()
      .split('\n')
      .map((r) => r.trim())
      .filter(Boolean);

    await this.api.updateSettings({
      openrouter_api_key: this.apiKeyInput().trim() || undefined,
      primary_model: this.primaryModel().trim(),
      fallback_model_1: this.fallbackModel1().trim(),
      fallback_model_2: this.fallbackModel2().trim(),
      max_tokens: this.maxTokens(),
      temperature: this.temperature(),
      trigger_word: this.defaultTriggerWord().trim(),
      trigger_position: this.defaultTriggerPos(),
      custom_rejection_regexes: customRegexes,
    });
    this.apiKeyInput.set('');
  }

  async saveNewSkillVersion(): Promise<void> {
    const saved = await this.api.saveSkillVersion({
      slug: this.editingSkillSlug().trim(),
      name: this.editingSkillName().trim(),
      training_type: this.editingTrainingType(),
      system_prompt_template: this.editingSystemPrompt(),
      user_prompt_template: this.editingUserPrompt(),
      min_words: this.editingMinWords(),
      max_words: this.editingMaxWords(),
    });
    this.selectSkillForEdit(saved);
  }
}
