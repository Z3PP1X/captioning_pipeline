import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { LEGACY_OPENROUTER_KEY_STORAGE, SettingsService } from './settings.service';
import { ApiService } from './api.service';

describe('SettingsService (OpenRouter key, server-only)', () => {
  let service: SettingsService;
  let api: { get: ReturnType<typeof vi.fn>; put: ReturnType<typeof vi.fn> };

  const serverSettings = (keySet: boolean) => ({
    settings: { default_k: 5, default_rule: 'person', sample_fps: 4, survivor_frac: 0.5, trigger_word: '', max_edge: 2048, openrouter_api_key_set: keySet },
  });

  beforeEach(() => {
    api = { get: vi.fn(), put: vi.fn() };
    TestBed.configureTestingModule({ providers: [SettingsService, { provide: ApiService, useValue: api }] });
    service = TestBed.inject(SettingsService);
    localStorage.clear();
  });

  afterEach(() => localStorage.clear());

  it('stores a key via PUT /api/settings and keeps only the *_set flag locally', async () => {
    api.put.mockResolvedValue(serverSettings(true));
    await service.saveOpenRouterKey('  sk-or-v1-abc  ');
    expect(api.put).toHaveBeenCalledWith('/api/settings', { openrouter_api_key: 'sk-or-v1-abc' });
    expect(service.settings().openrouter_api_key_set).toBe(true);
    expect(service.settings().openrouter_api_key).toBeUndefined();
  });

  it('moves a legacy localStorage key to the server once and deletes it', async () => {
    localStorage.setItem(LEGACY_OPENROUTER_KEY_STORAGE, 'sk-legacy');
    api.get.mockImplementation((path: string) =>
      Promise.resolve(path === '/api/settings' ? serverSettings(false) : { models: [] })
    );
    api.put.mockResolvedValue(serverSettings(true));

    await service.loadSettings();

    expect(api.put).toHaveBeenCalledWith('/api/settings', { openrouter_api_key: 'sk-legacy' });
    expect(localStorage.getItem(LEGACY_OPENROUTER_KEY_STORAGE)).toBeNull();
    expect(service.settings().openrouter_api_key_set).toBe(true);
  });

  it('only deletes the legacy key when the server already has one', async () => {
    localStorage.setItem(LEGACY_OPENROUTER_KEY_STORAGE, 'sk-legacy');
    api.get.mockImplementation((path: string) =>
      Promise.resolve(path === '/api/settings' ? serverSettings(true) : { models: [] })
    );

    await service.loadSettings();

    expect(api.put).not.toHaveBeenCalled();
    expect(localStorage.getItem(LEGACY_OPENROUTER_KEY_STORAGE)).toBeNull();
  });

  it('does not round-trip the read-only *_set flag when saving settings', async () => {
    service.settings.update((s) => ({ ...s, openrouter_api_key_set: true }));
    api.put.mockResolvedValue({});
    await service.saveSettings({ default_k: 7 });
    const payload = api.put.mock.calls[0][1];
    expect(payload.default_k).toBe(7);
    expect('openrouter_api_key_set' in payload).toBe(false);
    expect(service.settings().openrouter_api_key_set).toBe(true);
  });
});
