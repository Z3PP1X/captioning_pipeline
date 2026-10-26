import { Injectable, inject } from '@angular/core';
import { CurateService } from './curate.service';
import { UiStateService } from './ui-state.service';

export type HotkeyAction =
  | 'play_pause'
  | 'pause'
  | 'seek_back_1'
  | 'seek_fwd_1'
  | 'seek_back_2'
  | 'seek_fwd_2'
  | 'seek_back_10'
  | 'seek_fwd_10'
  | 'seek_back_60'
  | 'seek_fwd_60'
  | 'seek_back_300'
  | 'seek_fwd_300'
  | 'frame_back'
  | 'frame_fwd'
  | 'mark_in'
  | 'mark_out'
  | 'add_chunk'
  | 'toggle_snap_cuts'
  | 'jump_focus'
  | 'next_video'
  | 'keep'
  | 'reject'
  | 'star'
  | 'escape'
  | 'lightbox_prev'
  | 'lightbox_next'
  | 'tag_region'
  // Prompt Director: copy the 1st/2nd/3rd archetype card (registered only while that view is open)
  | 'copy_prompt_1'
  | 'copy_prompt_2'
  | 'copy_prompt_3';

interface KeyBinding {
  prevent?: boolean;
  resolve: (e: KeyboardEvent, isLightbox: boolean) => HotkeyAction | null;
}

function isTextEntry(el: Element): boolean {
  return ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) || (el as HTMLElement).isContentEditable;
}

/** Leave Ctrl/Cmd chords (copy, reload, find...) to the browser; Ctrl+Arrow is ours */
function isBrowserChord(e: KeyboardEvent): boolean {
  const isArrow = e.key === 'ArrowLeft' || e.key === 'ArrowRight';
  return (e.ctrlKey || e.metaKey) && !isArrow;
}

const fixed = (action: HotkeyAction, prevent = false): KeyBinding => ({ prevent, resolve: () => action });
const lightboxOnly = (action: HotkeyAction): KeyBinding => ({
  resolve: (_e, isLightbox) => (isLightbox ? action : null),
});

function arrowPair(e: KeyboardEvent, isLightbox: boolean): [HotkeyAction, HotkeyAction] {
  if (isLightbox) return ['lightbox_prev', 'lightbox_next'];
  if (e.altKey) return ['seek_back_300', 'seek_fwd_300'];
  if (e.ctrlKey || e.metaKey) return ['seek_back_60', 'seek_fwd_60'];
  if (e.shiftKey) return ['seek_back_10', 'seek_fwd_10'];
  return ['seek_back_1', 'seek_fwd_1'];
}

function resolveArrow(e: KeyboardEvent, isLightbox: boolean): HotkeyAction {
  const [back, fwd] = arrowPair(e, isLightbox);
  return e.key === 'ArrowLeft' ? back : fwd;
}

const arrow: KeyBinding = { prevent: true, resolve: resolveArrow };
const copyPrompt: KeyBinding = {
  resolve: (e) => (e.altKey ? null : (`copy_prompt_${e.key}` as HotkeyAction)),
};

/** Single-key bindings; letters are registered in both cases. */
const KEY_BINDINGS = new Map<string, KeyBinding>();
const bind = (keys: string[], binding: KeyBinding): void => {
  for (const key of keys) KEY_BINDINGS.set(key, binding);
};
const letter = (l: string): string[] => [l, l.toUpperCase()];

bind([' '], fixed('play_pause', true));
bind(letter('k'), { resolve: (_e, isLightbox) => (isLightbox ? 'keep' : 'pause') });
bind(letter('x'), lightboxOnly('reject'));
bind(letter('s'), lightboxOnly('star'));
bind(['Escape'], fixed('escape'));
bind(letter('g'), fixed('jump_focus', true));
bind(letter('j'), fixed('seek_back_2'));
bind(letter('l'), fixed('seek_fwd_2'));
bind(['PageUp'], fixed('seek_fwd_60', true));
bind(['PageDown'], fixed('seek_back_60', true));
bind(['ArrowLeft', 'ArrowRight'], arrow);
bind([','], fixed('frame_back', true));
bind(['.'], fixed('frame_fwd', true));
bind(['[', ...letter('i')], fixed('mark_in'));
bind([']', ...letter('o')], fixed('mark_out'));
bind(['Enter'], fixed('add_chunk'));
bind(letter('c'), fixed('toggle_snap_cuts'));
bind(letter('n'), fixed('next_video'));
bind(letter('t'), lightboxOnly('tag_region'));
bind(['1', '2', '3'], copyPrompt);

@Injectable({
  providedIn: 'root',
})
export class HotkeyService {
  private readonly curateService = inject(CurateService);
  private readonly ui = inject(UiStateService);
  private handlers = new Map<HotkeyAction, () => void>();

  register(action: HotkeyAction, handler: () => void): () => void {
    this.handlers.set(action, handler);
    return () => {
      if (this.handlers.get(action) === handler) {
        this.handlers.delete(action);
      }
    };
  }

  private isInteractiveTarget(el: Element | null): boolean {
    if (!el) return false;
    const tag = el.tagName;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return true;
    if ((el as HTMLElement).isContentEditable) return true;
    if (tag === 'BUTTON' || el.closest('button')) return true;
    if (tag === 'A' || el.closest('a')) return true;
    const role = el.getAttribute('role');
    if (role && ['button', 'checkbox', 'radio', 'menuitem', 'option', 'tab', 'switch'].includes(role)) return true;
    return false;
  }

  /**
   * Keymap per `studio-ui-spec` -> "Keyboard shortcuts". Guards, in order: focused text field (only Escape
   * blurs it), confirm dialog (owns the keyboard), any other registered modal (only Escape passes), focused
   * button/link (Space/Enter keep their native meaning), browser/OS chords (Ctrl/Cmd + letter).
   */
  handleKeyDown(e: KeyboardEvent): void {
    if (this.isBlockedByGuards(e, document.activeElement)) return;

    const binding = KEY_BINDINGS.get(e.key);
    if (!binding) return;
    if (binding.prevent) e.preventDefault();
    const action = binding.resolve(e, this.curateService.lightboxIndex() !== null);
    if (action) this.trigger(action);
  }

  /** Applies the focus/modal/chord guards; returns true when the key must not reach the keymap. */
  private isBlockedByGuards(e: KeyboardEvent, activeEl: Element | null): boolean {
    return (
      this.isBlockedByTextEntry(e, activeEl) ||
      // The confirm dialog handles its own keys (Escape cancels it via the CDK overlay)
      this.ui.isModalOpen('confirm') ||
      // While a modal is open only Escape reaches the app
      (this.ui.modalOpen() && e.key !== 'Escape') ||
      this.isBlockedByInteractiveTarget(e, activeEl) ||
      isBrowserChord(e)
    );
  }

  /** A focused text field swallows every key; Escape blurs it (unless the confirm dialog owns it). */
  private isBlockedByTextEntry(e: KeyboardEvent, activeEl: Element | null): boolean {
    if (!activeEl || !isTextEntry(activeEl)) return false;
    if (e.key === 'Escape' && !this.ui.isModalOpen('confirm')) {
      this.blurAndEscape(activeEl);
    }
    return true;
  }

  /** Do not steal Space or Enter from buttons, links, or custom controls */
  private isBlockedByInteractiveTarget(e: KeyboardEvent, activeEl: Element | null): boolean {
    if (!activeEl || !this.isInteractiveTarget(activeEl)) return false;
    if (e.key === ' ' || e.key === 'Enter') return true;
    if (e.key === 'Escape') {
      this.blurAndEscape(activeEl);
      return true;
    }
    return false;
  }

  private blurAndEscape(el: Element): void {
    (el as HTMLElement).blur();
    this.trigger('escape');
  }

  private trigger(action: HotkeyAction): void {
    const handler = this.handlers.get(action);
    if (handler) {
      handler();
    }
  }
}
