import { max, min, schema, validate } from '@angular/forms/signals';
import { PipelineSettings } from '../models/settings.model';

/** Numeric fields edited in the Settings dialog. */
export type NumericSettingKey =
  | 'default_k'
  | 'sample_fps'
  | 'survivor_frac'
  | 'max_edge'
  | 'snap_window_s'
  | 'tag_suggest_min_score'
  | 'dhash_dedup_max_distance';

export interface NumericFieldRule {
  label: string;
  min: number;
  max: number;
  step: number;
  integer?: boolean;
}

/**
 * Client-side ranges. They match the server's `SETTING_RANGES` (src/pipeline/config.py) so the dialog never
 * blocks a value the backend accepts and never sends one it rejects.
 */
export const NUMERIC_SETTING_RULES: Record<NumericSettingKey, NumericFieldRule> = {
  default_k: { label: 'Default frames per chunk (K)', min: 1, max: 100, step: 1, integer: true },
  sample_fps: { label: 'Sampling FPS', min: 0.1, max: 60, step: 0.5 },
  survivor_frac: { label: 'Survivor fraction', min: 0.01, max: 1, step: 0.05 },
  max_edge: { label: 'Max export dimension', min: 256, max: 8192, step: 16, integer: true },
  snap_window_s: { label: 'Snap window radius', min: 0, max: 10, step: 0.1 },
  tag_suggest_min_score: { label: 'Tag suggestion threshold', min: 0.05, max: 0.99, step: 0.05 },
  dhash_dedup_max_distance: { label: 'Near-duplicate distance (dHash)', min: 0, max: 32, step: 1, integer: true },
};

/** On/off pipeline switches edited in the Settings dialog (all default as in DEFAULT_USER_SETTINGS). */
export const BOOLEAN_SETTINGS = [
  { key: 'auto_suggest_tags', label: 'Pre-tag new extractions with learned image tags', default: true },
  { key: 'quality_v2', label: 'Quality v2 (hard gates + per-video percentiles)', default: true },
  { key: 'window_peak_sampling', label: 'Window-peak sampling (sharpest, stillest frame per window)', default: true },
  { key: 'reuse_analysis_cache', label: 'Reuse the analysis cache on re-extraction', default: true },
  { key: 'rag_auto_reindex', label: 'Update the Prompt Director index after captioning', default: true },
] as const;
export type BooleanSettingKey = (typeof BOOLEAN_SETTINGS)[number]['key'];

/** Choice settings edited in the Settings dialog; values match SETTING_CHOICES in config.py. */
export const CHOICE_SETTINGS = [
  {
    key: 'cut_detector',
    label: 'Scene cut detector',
    default: 'content',
    options: [
      { value: 'content', label: 'Frame-accurate content detector' },
      { value: 'legacy', label: 'Legacy keyframe cuts' },
    ],
  },
  {
    key: 'selection_embedder',
    label: 'Selection embedder',
    default: 'clip',
    options: [
      { value: 'clip', label: 'CLIP (built in)' },
      { value: 'dinov2', label: 'DINOv2-S (downloads ~24 MB)' },
    ],
  },
  {
    key: 'iqa_model',
    label: 'Image quality model',
    default: 'off',
    options: [
      { value: 'off', label: 'Off' },
      { value: 'clip_iqa', label: 'CLIP-IQA (no download)' },
      { value: 'topiq_nr', label: 'TOPIQ-NR (downloads ~177 MB)' },
    ],
  },
] as const;
export type ChoiceSettingKey = (typeof CHOICE_SETTINGS)[number]['key'];

/** Raw text of a number input -> number, or NaN when empty / not a number (never `|| default`). */
export function parseNumberInput(raw: string | number | null | undefined): number {
  if (typeof raw === 'number') return raw;
  const text = (raw ?? '').trim();
  if (text === '') return Number.NaN;
  return Number(text);
}

/** Error message for one numeric field, or null when valid. */
export function validateNumericSetting(key: NumericSettingKey, value: unknown): string | null {
  const rule = NUMERIC_SETTING_RULES[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return `${rule.label} must be a number.`;
  }
  if (rule.integer && !Number.isInteger(value)) {
    return `${rule.label} must be a whole number.`;
  }
  if (value < rule.min || value > rule.max) {
    return `${rule.label} must be between ${rule.min} and ${rule.max}.`;
  }
  return null;
}

/** All field errors of a settings form (empty object = valid). */
export function validateSettings(form: Partial<PipelineSettings>): Partial<Record<NumericSettingKey, string>> {
  const errors: Partial<Record<NumericSettingKey, string>> = {};
  for (const key of Object.keys(NUMERIC_SETTING_RULES) as NumericSettingKey[]) {
    const err = validateNumericSetting(key, form[key]);
    if (err) errors[key] = err;
  }
  return errors;
}

/** Model of the Settings dialog (Signal Forms). Numeric fields are `null` while the input is empty. */
export interface SettingsFormModel {
  default_k: number | null;
  default_rule: string;
  sample_fps: number | null;
  survivor_frac: number | null;
  trigger_word: string;
  max_edge: number | null;
  snap_to_cuts: boolean;
  snap_window_s: number | null;
  tag_suggest_min_score: number | null;
  dhash_dedup_max_distance: number | null;
  auto_suggest_tags: boolean;
  quality_v2: boolean;
  window_peak_sampling: boolean;
  reuse_analysis_cache: boolean;
  rag_auto_reindex: boolean;
  cut_detector: string;
  selection_embedder: string;
  iqa_model: string;
}

export const NUMERIC_SETTING_KEYS = Object.keys(NUMERIC_SETTING_RULES) as NumericSettingKey[];

function rangeMessage(rule: NumericFieldRule): string {
  return `${rule.label} must be between ${rule.min} and ${rule.max}.`;
}

/**
 * Signal Forms schema built from NUMERIC_SETTING_RULES. `min()`/`max()` carry the range (and set the native
 * min/max attributes through `[formField]`); `validate()` reports empty / NaN / non-integer values with the
 * same messages as `validateNumericSetting`.
 */
export const settingsFormSchema = schema<SettingsFormModel>((p) => {
  for (const key of NUMERIC_SETTING_KEYS) {
    const rule = NUMERIC_SETTING_RULES[key];
    validate(p[key], ({ value }) => {
      const v = value();
      const notANumber = typeof v !== 'number' || !Number.isFinite(v);
      if (!notANumber && !(rule.integer && !Number.isInteger(v))) return null;
      return { kind: notANumber ? 'number' : 'integer', message: validateNumericSetting(key, v) ?? undefined };
    });
    min(p[key], rule.min, { message: rangeMessage(rule) });
    max(p[key], rule.max, { message: rangeMessage(rule) });
  }
});

/**
 * The one message shown under a numeric field: type/integer problems win over the range (as in
 * `validateNumericSetting`); a native parse error (no message) reads as "must be a number".
 */
export function settingFieldMessage(
  key: NumericSettingKey,
  errors: readonly { kind: string; message?: string }[],
): string | null {
  if (errors.length === 0) return null;
  const err = errors.find((e) => e.kind !== 'min' && e.kind !== 'max') ?? errors[0];
  return err.message || `${NUMERIC_SETTING_RULES[key].label} must be a number.`;
}

/** Settings payload from a valid form model (numeric fields are non-null once the schema passes). */
export function settingsFromForm(model: SettingsFormModel): PipelineSettings {
  return {
    default_k: model.default_k as number,
    default_rule: model.default_rule,
    sample_fps: model.sample_fps as number,
    survivor_frac: model.survivor_frac as number,
    trigger_word: model.trigger_word,
    max_edge: model.max_edge as number,
    snap_to_cuts: model.snap_to_cuts,
    snap_window_s: model.snap_window_s as number,
    tag_suggest_min_score: model.tag_suggest_min_score as number,
    dhash_dedup_max_distance: model.dhash_dedup_max_distance as number,
    auto_suggest_tags: model.auto_suggest_tags,
    quality_v2: model.quality_v2,
    window_peak_sampling: model.window_peak_sampling,
    reuse_analysis_cache: model.reuse_analysis_cache,
    rag_auto_reindex: model.rag_auto_reindex,
    cut_detector: model.cut_detector as PipelineSettings['cut_detector'],
    selection_embedder: model.selection_embedder as PipelineSettings['selection_embedder'],
    iqa_model: model.iqa_model as PipelineSettings['iqa_model'],
  };
}

/** The dialog's defaults for settings that are missing from the stored settings. */
const SETTINGS_FORM_DEFAULTS: Readonly<SettingsFormModel> = {
  default_k: 5,
  default_rule: 'person',
  sample_fps: 4.0,
  survivor_frac: 0.5,
  trigger_word: '',
  max_edge: 2048,
  snap_to_cuts: true,
  snap_window_s: 1.5,
  tag_suggest_min_score: 0.5,
  dhash_dedup_max_distance: 4,
  auto_suggest_tags: true,
  quality_v2: true,
  window_peak_sampling: true,
  reuse_analysis_cache: true,
  rag_auto_reindex: true,
  cut_detector: 'content',
  selection_embedder: 'clip',
  iqa_model: 'off',
};

/** Form model from the stored settings, with the dialog's defaults for missing values. */
export function settingsToForm(s: Partial<PipelineSettings>): SettingsFormModel {
  const form: Record<string, unknown> = {};
  for (const key of Object.keys(SETTINGS_FORM_DEFAULTS) as (keyof SettingsFormModel)[]) {
    form[key] = s[key] ?? SETTINGS_FORM_DEFAULTS[key];
  }
  return form as unknown as SettingsFormModel;
}
