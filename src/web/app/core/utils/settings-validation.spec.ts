import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { describe, it, expect } from 'vitest';
import {
  NUMERIC_SETTING_KEYS,
  NumericSettingKey,
  SettingsFormModel,
  parseNumberInput,
  settingFieldMessage,
  settingsFormSchema,
  settingsFromForm,
  settingsToForm,
  validateNumericSetting,
  validateSettings,
} from './settings-validation';
import { PipelineSettings } from '../models/settings.model';

const valid: PipelineSettings = {
  default_k: 5,
  default_rule: 'person',
  sample_fps: 4,
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

describe('settings validation', () => {
  it('accepts the defaults', () => {
    expect(validateSettings(valid)).toEqual({});
  });

  it('parses empty input as NaN (not 0 / default) and keeps legitimate 0', () => {
    expect(parseNumberInput('')).toBeNaN();
    expect(parseNumberInput('  ')).toBeNaN();
    expect(parseNumberInput('abc')).toBeNaN();
    expect(parseNumberInput('0')).toBe(0);
    expect(parseNumberInput('1.5')).toBe(1.5);
  });

  it('rejects NaN and non-finite values', () => {
    expect(validateNumericSetting('sample_fps', Number.NaN)).toMatch(/must be a number/);
    expect(validateNumericSetting('sample_fps', Number.POSITIVE_INFINITY)).toMatch(/must be a number/);
    expect(validateNumericSetting('sample_fps', undefined)).toMatch(/must be a number/);
  });

  it('enforces min/max per field', () => {
    expect(validateNumericSetting('default_k', 0)).toMatch(/between 1 and 100/);
    expect(validateNumericSetting('default_k', 101)).toMatch(/between 1 and 100/);
    expect(validateNumericSetting('survivor_frac', 1.2)).toMatch(/between 0.01 and 1/);
    expect(validateNumericSetting('max_edge', 128)).toMatch(/between 256 and 8192/);
    expect(validateNumericSetting('snap_window_s', 0)).toBeNull();
    expect(validateNumericSetting('snap_window_s', 10.5)).toMatch(/between 0 and 10/);
  });

  it('requires whole numbers for K and max edge', () => {
    expect(validateNumericSetting('default_k', 5.5)).toMatch(/whole number/);
    expect(validateNumericSetting('max_edge', 1000.5)).toMatch(/whole number/);
    expect(validateNumericSetting('sample_fps', 2.5)).toBeNull();
  });

  it('reports every invalid field at once', () => {
    const errors = validateSettings({ ...valid, default_k: Number.NaN, max_edge: 99999 });
    expect(Object.keys(errors).sort()).toEqual(['default_k', 'max_edge']);
  });
});

describe('settingsFormSchema (Signal Forms)', () => {
  const makeForm = (model: SettingsFormModel) =>
    TestBed.runInInjectionContext(() => {
      const m = signal(model);
      return { model: m, f: form(m, settingsFormSchema) };
    });

  it('is valid for the defaults and round-trips the payload', () => {
    const { f, model } = makeForm(settingsToForm(valid));
    expect(f().invalid()).toBe(false);
    expect(settingsFromForm(model())).toEqual(valid);
  });

  it('fills the dialog defaults for missing settings', () => {
    expect(settingsToForm({})).toEqual({ ...valid, trigger_word: '' });
  });

  it('shows exactly the validateNumericSetting message for every rule violation', () => {
    const cases: [NumericSettingKey, number | null][] = [
      ['default_k', null],
      ['default_k', Number.NaN],
      ['default_k', 0],
      ['default_k', 101],
      ['default_k', 5.5],
      ['default_k', 0.5], // fractional and below min: the integer message wins, as before
      ['sample_fps', 0],
      ['sample_fps', 61],
      ['survivor_frac', 1.2],
      ['max_edge', 128],
      ['max_edge', 1000.5],
      ['snap_window_s', 10.5],
      ['snap_window_s', 0],
      ['sample_fps', 2.5],
    ];
    for (const [key, value] of cases) {
      const { f } = makeForm({ ...settingsToForm(valid), [key]: value });
      const expected = validateNumericSetting(key, value ?? Number.NaN);
      expect(settingFieldMessage(key, f[key]().errors()), `${key}=${value}`).toBe(expected);
      expect(f().invalid(), `${key}=${value}`).toBe(expected !== null);
    }
  });

  it('flags every invalid numeric field independently', () => {
    const { f } = makeForm({ ...settingsToForm(valid), default_k: null, max_edge: 99999 });
    const invalid = NUMERIC_SETTING_KEYS.filter((k) => f[k]().invalid());
    expect(invalid.sort()).toEqual(['default_k', 'max_edge']);
  });

  it('maps a native parse error (no message) to "must be a number"', () => {
    expect(settingFieldMessage('sample_fps', [{ kind: 'parse' }])).toBe('Sampling FPS must be a number.');
    expect(settingFieldMessage('sample_fps', [])).toBeNull();
  });
});

describe('pipeline settings in the dialog', () => {
  it('validates the tag threshold and the dHash distance like the server', () => {
    expect(validateNumericSetting('tag_suggest_min_score', 0.01)).toMatch(/between 0.05 and 0.99/);
    expect(validateNumericSetting('tag_suggest_min_score', 0.6)).toBeNull();
    expect(validateNumericSetting('dhash_dedup_max_distance', 2.5)).toMatch(/whole number/);
    expect(validateNumericSetting('dhash_dedup_max_distance', 33)).toMatch(/between 0 and 32/);
  });
});
