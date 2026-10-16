import { ChangeDetectionStrategy, Component, inject, linkedSignal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideTriangleAlert } from '@ng-icons/lucide';
import { HlmAlertDialogImports } from '@spartan-ng/helm/alert-dialog';
import { ConfirmRequest, ConfirmService } from '../../core/services/confirm.service';

/** Single app-wide confirmation dialog driven by ConfirmService (replaces window.confirm and ad-hoc overlays). */
@Component({
  selector: 'app-confirm-dialog',
  imports: [NgIcon, HlmAlertDialogImports],
  providers: [provideIcons({ lucideTriangleAlert })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-alert-dialog [state]="confirm.pending() ? 'open' : 'closed'" (closed)="confirm.settle(false)">
      <hlm-alert-dialog-content *hlmAlertDialogPortal="let ctx">
        @if (shown(); as req) {
          <hlm-alert-dialog-header>
            <h2 hlmAlertDialogTitle class="flex items-center gap-2">
              @if (req.destructive) {
                <ng-icon name="lucideTriangleAlert" class="size-4 text-destructive" aria-hidden="true" />
              }
              <span>{{ req.title }}</span>
            </h2>
            <p hlmAlertDialogDescription>{{ req.description }}</p>
          </hlm-alert-dialog-header>
          <hlm-alert-dialog-footer>
            <button hlmAlertDialogCancel>{{ req.cancelLabel || 'Cancel' }}</button>
            <button
              hlmAlertDialogAction
              [variant]="req.destructive ? 'destructive' : 'default'"
              (click)="confirm.settle(true)"
            >
              {{ req.confirmLabel || 'Confirm' }}
            </button>
          </hlm-alert-dialog-footer>
        }
      </hlm-alert-dialog-content>
    </hlm-alert-dialog>
  `,
})
export class ConfirmDialogComponent {
  readonly confirm = inject(ConfirmService);

  /** Keeps the last request rendered while the dialog animates out. */
  readonly shown = linkedSignal<ConfirmRequest | null, ConfirmRequest | null>({
    source: () => this.confirm.pending(),
    computation: (next, previous) => next ?? previous?.value ?? null,
  });
}
