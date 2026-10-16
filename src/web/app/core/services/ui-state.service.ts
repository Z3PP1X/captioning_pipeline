import { DestroyRef, Injectable, computed, effect, inject, signal } from '@angular/core';

/** Ids of the overlays that block global shortcuts while open (the lightbox is handled by HotkeyService itself). */
export type ModalId = 'settings' | 'openrouter' | 'person' | 'tag-manager' | 'confirm' | 'reselect';

/**
 * Small cross-cutting UI state. Modals report themselves here so global keyboard shortcuts can be suppressed
 * while one is open (only Escape still reaches the app).
 */
@Injectable({
  providedIn: 'root',
})
export class UiStateService {
  private readonly openModals = signal<ReadonlySet<ModalId>>(new Set());

  /** True while any registered modal is open. */
  readonly modalOpen = computed(() => this.openModals().size > 0);

  isModalOpen(id: ModalId): boolean {
    return this.openModals().has(id);
  }

  setModalOpen(id: ModalId, open: boolean): void {
    const current = this.openModals();
    if (current.has(id) === open) return;
    const next = new Set(current);
    if (open) next.add(id);
    else next.delete(id);
    this.openModals.set(next);
  }
}

/**
 * Registers the calling modal component with UiStateService. Call from a constructor or field initializer.
 * `isOpen` defaults to "open while the component exists" (for modals rendered behind an `@if`).
 */
export function registerModal(id: ModalId, isOpen: () => boolean = () => true): void {
  const ui = inject(UiStateService);
  effect(() => ui.setModalOpen(id, isOpen()));
  inject(DestroyRef).onDestroy(() => ui.setModalOpen(id, false));
}
