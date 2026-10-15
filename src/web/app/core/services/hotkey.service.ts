import { Injectable, inject } from '@angular/core';
import { CurateService } from './curate.service';

export type HotkeyAction =
  | 'play_pause'
  | 'seek_back_1'
  | 'seek_fwd_1'
  | 'seek_back_10'
  | 'seek_fwd_10'
  | 'seek_back_60'
  | 'seek_fwd_60'
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
  | 'lightbox_next';

@Injectable({
  providedIn: 'root',
})
export class HotkeyService {
  private readonly curateService = inject(CurateService);
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

  handleKeyDown(e: KeyboardEvent): void {
    const activeEl = document.activeElement;
    if (
      activeEl &&
      (['INPUT', 'SELECT', 'TEXTAREA'].includes(activeEl.tagName) || (activeEl as HTMLElement).isContentEditable)
    ) {
      if (e.key === 'Escape') {
        (activeEl as HTMLElement).blur();
        this.trigger('escape');
      }
      return;
    }

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

    const isLightbox = this.curateService.lightboxIndex() !== null;

    switch (e.key) {
      case ' ':
        e.preventDefault();
        this.trigger('play_pause');
        break;
      case 'k':
      case 'K':
        this.trigger('keep');
        break;
      case 'x':
      case 'X':
        this.trigger('reject');
        break;
      case 's':
      case 'S':
        this.trigger('star');
        break;
      case 'Escape':
        this.trigger('escape');
        break;
      case 'g':
      case 'G':
        e.preventDefault();
        this.trigger('jump_focus');
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
        e.preventDefault();
        if (isLightbox) {
          this.trigger('lightbox_prev');
        } else {
          if (e.ctrlKey) this.trigger('seek_back_60');
          else if (e.shiftKey) this.trigger('seek_back_10');
          else this.trigger('seek_back_1');
        }
        break;
      case 'ArrowRight':
        e.preventDefault();
        if (isLightbox) {
          this.trigger('lightbox_next');
        } else {
          if (e.ctrlKey) this.trigger('seek_fwd_60');
          else if (e.shiftKey) this.trigger('seek_fwd_10');
          else this.trigger('seek_fwd_1');
        }
        break;
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
    }
  }

  private trigger(action: HotkeyAction): void {
    const handler = this.handlers.get(action);
    if (handler) {
      handler();
    }
  }
}
