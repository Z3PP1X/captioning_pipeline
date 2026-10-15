import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { HotkeyService } from './hotkey.service';
import { CurateService } from './curate.service';
import { ApiService } from './api.service';

describe('HotkeyService', () => {
  let service: HotkeyService;
  let curateService: CurateService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        HotkeyService,
        CurateService,
        { provide: ApiService, useValue: { get: vi.fn(), post: vi.fn() } },
      ],
    });

    service = TestBed.inject(HotkeyService);
    curateService = TestBed.inject(CurateService);
  });

  it('registers and unregisters hotkey handlers', () => {
    const handler = vi.fn();
    const unregister = service.register('play_pause', handler);

    const event = new KeyboardEvent('keydown', { key: ' ' });
    service.handleKeyDown(event);
    expect(handler).toHaveBeenCalledTimes(1);

    unregister();
    service.handleKeyDown(event);
    expect(handler).toHaveBeenCalledTimes(1); // not called again
  });

  it('does NOT intercept Space or Enter when focused on a button', () => {
    const playHandler = vi.fn();
    service.register('play_pause', playHandler);

    const button = document.createElement('button');
    document.body.appendChild(button);
    button.focus();

    const event = new KeyboardEvent('keydown', { key: ' ' });
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault');

    service.handleKeyDown(event);

    expect(preventDefaultSpy).not.toHaveBeenCalled();
    expect(playHandler).not.toHaveBeenCalled();

    document.body.removeChild(button);
  });

  it('does NOT trigger hotkeys when focused inside an input or textarea', () => {
    const keepHandler = vi.fn();
    service.register('keep', keepHandler);

    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();

    const event = new KeyboardEvent('keydown', { key: 'k' });
    service.handleKeyDown(event);

    expect(keepHandler).not.toHaveBeenCalled();

    document.body.removeChild(input);
  });

  it('triggers lightbox_prev and lightbox_next when lightbox is open', () => {
    curateService.lightboxIndex.set(2);

    const prevHandler = vi.fn();
    const nextHandler = vi.fn();
    const seekBackHandler = vi.fn();

    service.register('lightbox_prev', prevHandler);
    service.register('lightbox_next', nextHandler);
    service.register('seek_back_1', seekBackHandler);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
    expect(prevHandler).toHaveBeenCalledTimes(1);
    expect(seekBackHandler).not.toHaveBeenCalled();

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
    expect(nextHandler).toHaveBeenCalledTimes(1);
  });

  it('triggers seek actions when lightbox is closed', () => {
    curateService.lightboxIndex.set(null);

    const seekBackHandler = vi.fn();
    const seekBack10Handler = vi.fn();
    const seekBack60Handler = vi.fn();

    service.register('seek_back_1', seekBackHandler);
    service.register('seek_back_10', seekBack10Handler);
    service.register('seek_back_60', seekBack60Handler);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
    expect(seekBackHandler).toHaveBeenCalledTimes(1);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowLeft', shiftKey: true }));
    expect(seekBack10Handler).toHaveBeenCalledTimes(1);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowLeft', ctrlKey: true }));
    expect(seekBack60Handler).toHaveBeenCalledTimes(1);
  });

  it('triggers keep, reject, star on K, X, S keys', () => {
    const keepHandler = vi.fn();
    const rejectHandler = vi.fn();
    const starHandler = vi.fn();

    service.register('keep', keepHandler);
    service.register('reject', rejectHandler);
    service.register('star', starHandler);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'k' }));
    expect(keepHandler).toHaveBeenCalledTimes(1);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 'X' }));
    expect(rejectHandler).toHaveBeenCalledTimes(1);

    service.handleKeyDown(new KeyboardEvent('keydown', { key: 's' }));
    expect(starHandler).toHaveBeenCalledTimes(1);
  });
});
