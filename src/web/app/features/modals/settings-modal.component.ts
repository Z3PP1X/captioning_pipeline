import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormField, form, submit } from '@angular/forms/signals';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideSave, lucideTriangleAlert } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmCheckboxImports } from '@spartan-ng/helm/checkbox';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { SettingsService } from '../../core/services/settings.service';
import { JobEventService } from '../../core/services/job-event.service';
import { registerModal } from '../../core/services/ui-state.service';
import {
  BOOLEAN_SETTINGS,
  CHOICE_SETTINGS,
  NUMERIC_SETTING_KEYS,
  NUMERIC_SETTING_RULES,
  NumericSettingKey,
  SettingsFormModel,
  settingFieldMessage,
  settingsFormSchema,
  settingsFromForm,
  settingsToForm,
} from '../../core/utils/settings-validation';

interface NumericFieldView {
  key: NumericSettingKey;
  id: string;
  label: string;
  help?: string;
}

@Component({
  selector: 'app-settings-modal',
  imports: [
    NgIcon,
    FormField,
    HlmButtonImports,
    HlmCheckboxImports,
    HlmDialogImports,
    HlmInputImports,
    HlmNativeSelectImports,
  ],
  providers: [provideIcons({ lucideSave, lucideTriangleAlert })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-dialog [state]="isOpen() ? 'open' : 'closed'" (closed)="close.emit()">
      <hlm-dialog-content *hlmDialogPortal="let ctx" class="sm:max-w-lg gap-0" closeLabel="Close settings">
        <hlm-dialog-header class="border-b border-border pb-3 mb-4 pr-8">
          <h3 hlmDialogTitle class="font-sans font-bold text-base text-foreground">Pipeline settings</h3>
        </hlm-dialog-header>

        <!-- Body form fields (Signal Forms: settingsFormSchema) -->
        <form
          class="flex flex-col gap-3 text-xs max-h-[70vh] overflow-y-auto pr-1"
          novalidate
          (submit)="$event.preventDefault(); onSave()"
        >
          @for (f of numericFields; track f.key) {
            @let err = errors()[f.key];
            <div class="flex flex-col gap-1">
              <label [for]="f.id" class="font-medium text-foreground">{{ f.label }}</label>
              <input
                hlmInput
                [id]="f.id"
                type="number"
                inputmode="decimal"
                [attr.step]="rules[f.key].step"
                [formField]="settingsForm[f.key]"
                [forceInvalid]="!!err"
                [aria-describedby]="err ? f.id + '-error' : f.help ? f.id + '-help' : null"
                class="h-8 md:h-8 px-3 text-xs md:text-xs tabular-nums"
              />
              @if (err) {
                <span [id]="f.id + '-error'" class="flex items-center gap-1 text-xs font-medium text-destructive">
                  <ng-icon name="lucideTriangleAlert" class="size-3 shrink-0" aria-hidden="true" />
                  {{ err }}
                </span>
              } @else if (f.help) {
                <span [id]="f.id + '-help'" class="text-xs text-muted-foreground">{{ f.help }}</span>
              }
            </div>

            @if (f.key === 'default_k') {
              <div class="flex flex-col gap-1">
                <label for="setting-default-rule" class="font-medium text-foreground">Default content rule</label>
                <hlm-native-select size="sm" selectId="setting-default-rule" selectClass="text-xs" [formField]="settingsForm.default_rule">
                  <option value="person">Person</option>
                  <option value="equipment">Equipment</option>
                  <option value="both">Both</option>
                  <option value="either">Either</option>
                  <option value="none">None (pure quality)</option>
                </hlm-native-select>
              </div>
            }

            @if (f.key === 'survivor_frac') {
              <div class="flex flex-col gap-1">
                <label for="setting-trigger-word" class="font-medium text-foreground">Default trigger word</label>
                <input
                  hlmInput
                  id="setting-trigger-word"
                  type="text"
                  placeholder="e.g. restr_elg"
                  [formField]="settingsForm.trigger_word"
                  class="h-8 md:h-8 px-3 text-xs md:text-xs"
                />
              </div>
            }

            @if (f.key === 'max_edge') {
              <!-- Snap to cuts toggle (the snap window follows) -->
              <div class="flex items-center justify-between gap-3 p-2 rounded-md border border-border bg-muted/20">
                <div class="flex flex-col">
                  <label for="setting-snap-to-cuts" class="font-medium text-foreground cursor-pointer">Snap chunk points to cuts</label>
                  <span class="text-xs text-muted-foreground">Magnetically snap chunk In/Out to scene cuts</span>
                </div>
                <hlm-checkbox inputId="setting-snap-to-cuts" [formField]="settingsForm.snap_to_cuts" />
              </div>
            }
          }
          <!-- Pipeline & learning (image tags, Phase F, Prompt Director) -->
          <fieldset class="flex flex-col gap-3 rounded-md border border-border p-3">
            <legend class="px-1 font-semibold text-foreground">Pipeline and learning</legend>
            @for (c of choiceSettings; track c.key) {
              <div class="flex flex-col gap-1">
                <label [for]="'setting-' + c.key" class="font-medium text-foreground">{{ c.label }}</label>
                <hlm-native-select size="sm" [selectId]="'setting-' + c.key" selectClass="text-xs" [formField]="settingsForm[c.key]">
                  @for (o of c.options; track o.value) {
                    <option [value]="o.value">{{ o.label }}</option>
                  }
                </hlm-native-select>
              </div>
            }
            @for (b of booleanSettings; track b.key) {
              <div class="flex items-center justify-between gap-3">
                <label [for]="'setting-' + b.key" class="text-foreground cursor-pointer">{{ b.label }}</label>
                <hlm-checkbox [inputId]="'setting-' + b.key" [formField]="settingsForm[b.key]" />
              </div>
            }
          </fieldset>
        </form>

        <!-- Footer actions -->
        <hlm-dialog-footer class="items-center border-t border-border pt-4 mt-5">
          @if (!isValid()) {
            <span class="sm:mr-auto text-xs font-medium text-destructive" role="status">
              Fix {{ errorCount() }} {{ errorCount() === 1 ? 'field' : 'fields' }} to save.
            </span>
          }
          <button hlmBtn variant="outline" size="sm" (click)="close.emit()" class="cursor-pointer">Cancel</button>
          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="onSave()"
            [disabled]="!isValid() || isSaving()"
            class="cursor-pointer gap-1.5 font-bold"
          >
            <ng-icon name="lucideSave" class="size-3.5" />
            <span>Save settings</span>
          </button>
        </hlm-dialog-footer>
      </hlm-dialog-content>
    </hlm-dialog>
  `,
})
export class SettingsModalComponent {
  private readonly settingsService = inject(SettingsService);
  private readonly jobEvents = inject(JobEventService);

  readonly isOpen = input<boolean>(false);
  readonly close = output<void>();

  readonly rules = NUMERIC_SETTING_RULES;
  readonly numericFields: NumericFieldView[] = [
    { key: 'default_k', id: 'setting-default-k', label: 'Default frames per chunk (K)' },
    { key: 'sample_fps', id: 'setting-sample-fps', label: 'Sampling FPS during extraction' },
    {
      key: 'survivor_frac',
      id: 'setting-survivor-frac',
      label: 'Survivor fraction to classifier',
      help: 'Fraction of sampled frames kept before scoring (0.5 = 50%)',
    },
    { key: 'max_edge', id: 'setting-max-edge', label: 'Max export dimension (px)' },
    { key: 'snap_window_s', id: 'setting-snap-window', label: 'Snap window radius (seconds)' },
    {
      key: 'tag_suggest_min_score',
      id: 'setting-tag-threshold',
      label: 'Image tag suggestion threshold (0-1)',
      help: 'Higher = fewer, surer suggestions',
    },
    {
      key: 'dhash_dedup_max_distance',
      id: 'setting-dhash',
      label: 'Near-duplicate distance (dHash bits, 0 = off)',
      help: 'Consecutive samples closer than this are dropped before scoring',
    },
  ];
  readonly booleanSettings = BOOLEAN_SETTINGS;
  readonly choiceSettings = CHOICE_SETTINGS;

  /** Form model; the field tree below is the single source of values, validity and touched state. */
  readonly formModel = signal<SettingsFormModel>(settingsToForm({}));
  readonly settingsForm = form(this.formModel, settingsFormSchema);
  readonly isSaving = signal(false);

  /** One message per invalid numeric field (shown immediately, as before; Save stays disabled). */
  readonly errors = computed(() => {
    const out: Partial<Record<NumericSettingKey, string>> = {};
    for (const key of NUMERIC_SETTING_KEYS) {
      const msg = settingFieldMessage(key, this.settingsForm[key]().errors());
      if (msg) out[key] = msg;
    }
    return out;
  });
  readonly errorCount = computed(() => Object.keys(this.errors()).length);
  readonly isValid = computed(() => !this.settingsForm().invalid());

  constructor() {
    registerModal('settings', () => this.isOpen());

    // Every open starts from the stored settings with a pristine form
    effect(() => {
      if (this.isOpen()) {
        const s = untracked(() => this.settingsService.settings());
        untracked(() => this.settingsForm().reset(settingsToForm(s)));
      }
    });
  }

  async onSave(): Promise<void> {
    if (this.isSaving()) return;
    // submit() marks every field touched and only runs the action when the schema passes (never PUTs NaN)
    await submit(this.settingsForm, async () => {
      this.isSaving.set(true);
      try {
        await this.settingsService.saveSettings(settingsFromForm(this.formModel()));
        this.close.emit();
      } catch (err) {
        this.jobEvents.showToast('Settings not saved', err instanceof Error ? err.message : String(err), null, true);
      } finally {
        this.isSaving.set(false);
      }
      return undefined;
    });
  }
}
