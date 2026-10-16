import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { HotkeyService } from './hotkey.service';
import { CurateService } from './curate.service';
import { ApiService } from './api.service';
import { UiStateService } from './ui-state.service';

describe('HotkeyService', () => {
  let service: HotkeyService;
  let curateService: CurateService;
  let ui: UiStateService;

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
    ui = TestBed.inject(UiStateService);
  });

  const press = (key: string, init: KeyboardEventInit = {}) =>
    service.handleKeyDown(new KeyboardEvent('keydown', { key, ...init }));

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

  it('triggers keep, reject, star on K, X, S while the lightbox is open', () => {
    curateService.lightboxIndex.set(0);
    const keepHandler = vi.fn();
    const rejectHandler = vi.fn();
    const starHandler = vi.fn();

    service.register('keep', keepHandler);
    service.register('reject', rejectHandler);
    service.register('star', starHandler);

    press('k');
    expect(keepHandler).toHaveBeenCalledTimes(1);

    press('X');
    expect(rejectHandler).toHaveBeenCalledTimes(1);

    press('s');
    expect(starHandler).toHaveBeenCalledTimes(1);
  });

  it('maps K to pause and ignores X / S when the lightbox is closed', () => {
    curateService.lightboxIndex.set(null);
    const pause = vi.fn();
    const keep = vi.fn();
    const reject = vi.fn();
    const star = vi.fn();
    service.register('pause', pause);
    service.register('keep', keep);
    service.register('reject', reject);
    service.register('star', star);

    press('k');
    press('x');
    press('s');

    expect(pause).toHaveBeenCalledTimes(1);
    expect(keep).not.toHaveBeenCalled();
    expect(reject).not.toHaveBeenCalled();
    expect(star).not.toHaveBeenCalled();
  });

  it('maps J / L to -2 s / +2 s', () => {
    const back = vi.fn();
    const fwd = vi.fn();
    service.register('seek_back_2', back);
    service.register('seek_fwd_2', fwd);

    press('j');
    press('L');

    expect(back).toHaveBeenCalledTimes(1);
    expect(fwd).toHaveBeenCalledTimes(1);
  });

  it('maps Alt+Arrow to 300 s and Ctrl/Cmd+Arrow to 60 s', () => {
    const back300 = vi.fn();
    const fwd300 = vi.fn();
    const fwd60 = vi.fn();
    service.register('seek_back_300', back300);
    service.register('seek_fwd_300', fwd300);
    service.register('seek_fwd_60', fwd60);

    press('ArrowLeft', { altKey: true });
    press('ArrowRight', { altKey: true });
    press('ArrowRight', { metaKey: true });

    expect(back300).toHaveBeenCalledTimes(1);
    expect(fwd300).toHaveBeenCalledTimes(1);
    expect(fwd60).toHaveBeenCalledTimes(1);
  });

  it('leaves Ctrl/Cmd + letter chords to the browser', () => {
    const snap = vi.fn();
    service.register('toggle_snap_cuts', snap);

    press('c', { metaKey: true });
    press('c', { ctrlKey: true });
    expect(snap).not.toHaveBeenCalled();

    press('c');
    expect(snap).toHaveBeenCalledTimes(1);
  });

  it('maps the remaining spec keys (PageUp/Down, , . [ ] I O Enter C N G)', () => {
    const actions = [
      'seek_fwd_60',
      'seek_back_60',
      'frame_back',
      'frame_fwd',
      'mark_in',
      'mark_out',
      'add_chunk',
      'toggle_snap_cuts',
      'next_video',
      'jump_focus',
    ] as const;
    const handlers = Object.fromEntries(actions.map((a) => [a, vi.fn()]));
    for (const a of actions) service.register(a, handlers[a]);

    for (const key of ['PageUp', 'PageDown', ',', '.', '[', 'i', ']', 'O', 'Enter', 'c', 'n', 'g']) {
      press(key);
    }

    expect(handlers['seek_fwd_60']).toHaveBeenCalledTimes(1);
    expect(handlers['seek_back_60']).toHaveBeenCalledTimes(1);
    expect(handlers['frame_back']).toHaveBeenCalledTimes(1);
    expect(handlers['frame_fwd']).toHaveBeenCalledTimes(1);
    expect(handlers['mark_in']).toHaveBeenCalledTimes(2);
    expect(handlers['mark_out']).toHaveBeenCalledTimes(2);
    expect(handlers['add_chunk']).toHaveBeenCalledTimes(1);
    expect(handlers['toggle_snap_cuts']).toHaveBeenCalledTimes(1);
    expect(handlers['next_video']).toHaveBeenCalledTimes(1);
    expect(handlers['jump_focus']).toHaveBeenCalledTimes(1);
  });

  describe('modal suppression', () => {
    it('suppresses every shortcut except Escape while a modal is open', () => {
      const play = vi.fn();
      const next = vi.fn();
      const escape = vi.fn();
      service.register('play_pause', play);
      service.register('next_video', next);
      service.register('escape', escape);

      ui.setModalOpen('settings', true);
      press(' ');
      press('n');
      press('Escape');

      expect(play).not.toHaveBeenCalled();
      expect(next).not.toHaveBeenCalled();
      expect(escape).toHaveBeenCalledTimes(1);

      ui.setModalOpen('settings', false);
      press('n');
      expect(next).toHaveBeenCalledTimes(1);
    });

    it('stays suppressed until every open modal has closed', () => {
      const next = vi.fn();
      service.register('next_video', next);

      ui.setModalOpen('person', true);
      ui.setModalOpen('tag-manager', true);
      ui.setModalOpen('person', false);
      press('n');
      expect(next).not.toHaveBeenCalled();

      ui.setModalOpen('tag-manager', false);
      press('n');
      expect(next).toHaveBeenCalledTimes(1);
    });

    it('lets the confirm dialog own the keyboard, including Escape', () => {
      const escape = vi.fn();
      service.register('escape', escape);

      ui.setModalOpen('confirm', true);
      press('Escape');
      expect(escape).not.toHaveBeenCalled();
    });

    it('keeps lightbox-only T (tag region) working while the lightbox is open', () => {
      const tag = vi.fn();
      service.register('tag_region', tag);

      press('t');
      expect(tag).not.toHaveBeenCalled();

      curateService.lightboxIndex.set(1);
      press('t');
      expect(tag).toHaveBeenCalledTimes(1);
    });

    it('keeps 1/2/3 copy-prompt shortcuts when registered', () => {
      const copy2 = vi.fn();
      service.register('copy_prompt_2', copy2);
      press('2');
      expect(copy2).toHaveBeenCalledTimes(1);

      ui.setModalOpen('openrouter', true);
      press('2');
      expect(copy2).toHaveBeenCalledTimes(1);
    });
  });
});
