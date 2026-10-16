import { Injectable, inject, signal } from '@angular/core';
import { UiStateService } from './ui-state.service';

export interface ConfirmRequest {
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive actions get the destructive button variant (default true). */
  destructive?: boolean;
}

interface PendingConfirm extends ConfirmRequest {
  resolve: (confirmed: boolean) => void;
}

/**
 * Promise-based replacement for window.confirm(), rendered by ConfirmDialogComponent (hlm-alert-dialog)
 * mounted once in the app shell.
 */
@Injectable({
  providedIn: 'root',
})
export class ConfirmService {
  private readonly ui = inject(UiStateService);
  readonly pending = signal<PendingConfirm | null>(null);

  ask(request: ConfirmRequest): Promise<boolean> {
    // Only one confirmation at a time: a new request cancels the previous one
    this.settle(false);
    return new Promise<boolean>((resolve) => {
      this.pending.set({ destructive: true, ...request, resolve });
      this.ui.setModalOpen('confirm', true);
    });
  }

  settle(confirmed: boolean): void {
    const current = this.pending();
    if (!current) return;
    this.pending.set(null);
    this.ui.setModalOpen('confirm', false);
    current.resolve(confirmed);
  }
}
