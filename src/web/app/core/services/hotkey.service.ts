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
    const activeEl = document.activeElement;
    if (
      activeEl &&
      (['INPUT', 'SELECT', 'TEXTAREA'].includes(activeEl.tagName) || (activeEl as HTMLElement).isContentEditable)
    ) {
      if (e.key === 'Escape' && !this.ui.isModalOpen('confirm')) {
        (activeEl as HTMLElement).blur();
        this.trigger('escape');
      }
      return;
    }

    // The confirm dialog handles its own keys (Escape cancels it via the CDK overlay)
    if (this.ui.isModalOpen('confirm')) return;

    // While a modal is open only Escape reaches the app
    if (this.ui.modalOpen() && e.key !== 'Escape') return;

    // Do not steal Space or Enter from buttons, links, or custom controls
    if (this.isInteractiveTarget(activeEl)) {
      if (e.key === ' ' || e.key === 'Enter') {
        return;
      }
      if (e.key === 'Escape') {
        (activeEl as HTMLElement).blur();
        this.trigger('escape');
        return;
      }
    }

    const isArrow = e.key === 'ArrowLeft' || e.key === 'ArrowRight';
    // Leave Ctrl/Cmd chords (copy, reload, find...) to the browser; Ctrl+Arrow is ours
    if ((e.ctrlKey || e.metaKey) && !isArrow) return;

    const isLightbox = this.curateService.lightboxIndex() !== null;

    switch (e.key) {
      case ' ':
        e.preventDefault();
        this.trigger('play_pause');
        break;
      case 'k':
      case 'K':
        this.trigger(isLightbox ? 'keep' : 'pause');
        break;
      case 'x':
      case 'X':
        if (isLightbox) this.trigger('reject');
        break;
      case 's':
      case 'S':
        if (isLightbox) this.trigger('star');
        break;
      case 'Escape':
        this.trigger('escape');
        break;
      case 'g':
      case 'G':
        e.preventDefault();
        this.trigger('jump_focus');
        break;
      case 'j':
      case 'J':
        this.trigger('seek_back_2');
        break;
      case 'l':
      case 'L':
        this.trigger('seek_fwd_2');
        break;
      case 'PageUp':
        e.preventDefault();
        this.trigger('seek_fwd_60');
        break;
      case 'PageDown':
        e.preventDefault();
        this.trigger('seek_back_60');
        break;
      case 'ArrowLeft':
      case 'ArrowRight': {
        e.preventDefault();
        const back = e.key === 'ArrowLeft';
        if (isLightbox) {
          this.trigger(back ? 'lightbox_prev' : 'lightbox_next');
        } else if (e.altKey) {
          this.trigger(back ? 'seek_back_300' : 'seek_fwd_300');
        } else if (e.ctrlKey || e.metaKey) {
          this.trigger(back ? 'seek_back_60' : 'seek_fwd_60');
        } else if (e.shiftKey) {
          this.trigger(back ? 'seek_back_10' : 'seek_fwd_10');
        } else {
          this.trigger(back ? 'seek_back_1' : 'seek_fwd_1');
        }
        break;
      }
      case ',':
        e.preventDefault();
        this.trigger('frame_back');
        break;
      case '.':
        e.preventDefault();
        this.trigger('frame_fwd');
        break;
      case '[':
      case 'i':
      case 'I':
        this.trigger('mark_in');
        break;
      case ']':
      case 'o':
      case 'O':
        this.trigger('mark_out');
        break;
      case 'Enter':
        this.trigger('add_chunk');
        break;
      case 'c':
      case 'C':
        this.trigger('toggle_snap_cuts');
        break;
      case 'n':
      case 'N':
        this.trigger('next_video');
        break;
      case 't':
      case 'T':
        if (isLightbox) this.trigger('tag_region');
        break;
      case '1':
      case '2':
      case '3':
        if (!e.altKey) {
          this.trigger(`copy_prompt_${e.key}` as HotkeyAction);
        }
        break;
    }
  }

  private trigger(action: HotkeyAction): void {
    const handler = this.handlers.get(action);
    if (handler) {
      handler();
    }
  }
}
