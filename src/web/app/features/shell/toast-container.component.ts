import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, output, untracked } from '@angular/core';
import { toast } from '@spartan-ng/brain/sonner';
import { HlmToasterImports } from '@spartan-ng/helm/sonner';
import { JobEventService, ToastAction, ToastNotification } from '../../core/services/job-event.service';

/**
 * Renders `JobEventService.toasts()` through the Spartan sonner (`hlm-toaster`).
 *
 * The service stays the single source of truth (`showToast` / `dismissToast`, auto-dismiss after 9 s, errors and
 * `persistent` toasts stay until closed); this component only mirrors that list into the sonner store:
 * - new entries become sonner toasts (`error` for errors, `success` when they carry an action, `info` otherwise)
 *   with `duration: Infinity`, so the service timer decides when a toast goes away;
 * - closing / swiping a toast calls `dismissToast`, entries removed by the service are dismissed in sonner;
 * - a typed `ToastAction` renders as the sonner action button and is emitted via `actionRequest`
 *   (the app shell navigates to `action.tab` and selects `action.videoId`).
 *
 * The sonner list is `position: fixed` with z-index 999999999, i.e. above the CDK overlay container (1000) that
 * hosts `hlm-dialog`, so toasts stay readable over open dialogs. `expand` keeps stacked toasts fully legible.
 */
@Component({
  selector: 'app-toast-container',
  imports: [HlmToasterImports],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-toaster
      position="bottom-right"
      [expand]="true"
      [closeButton]="true"
      [visibleToasts]="5"
      [toastOptions]="toastOptions"
    />
  `,
})
export class ToastContainerComponent {
  readonly jobEvents = inject(JobEventService);

  readonly actionRequest = output<ToastAction>();

  /**
   * Surface colours come from the `hlm-toaster` CSS variables (popover / border). Ink title, muted description;
   * errors add a destructive border and icon on top of the error icon shape (status never by colour alone).
   */
  protected readonly toastOptions = {
    classes: {
      toast: 'shadow-xl',
      title: 'text-sm font-semibold text-foreground',
      description: 'text-xs text-muted-foreground!',
      error: 'border-destructive! [&_[data-icon]]:text-destructive',
      closeButton: 'bg-popover! text-muted-foreground! hover:text-foreground! border-border!',
    },
  };

  /** Ids currently mirrored into the sonner store. */
  private readonly mirrored = new Set<string>();

  constructor() {
    effect(() => {
      const list = this.jobEvents.toasts();
      untracked(() => this.sync(list));
    });

    inject(DestroyRef).onDestroy(() => {
      for (const id of this.mirrored) toast.dismiss(id);
      this.mirrored.clear();
    });
  }

  private sync(list: ToastNotification[]): void {
    const ids = new Set(list.map((t) => t.id));
    for (const id of Array.from(this.mirrored)) {
      if (!ids.has(id)) {
        this.mirrored.delete(id);
        toast.dismiss(id);
      }
    }
    for (const t of list) {
      if (this.mirrored.has(t.id)) continue;
      this.mirrored.add(t.id);
      this.show(t);
    }
  }

  private show(t: ToastNotification): void {
    // Removed by sonner itself (close button, swipe, action click): drop it from the service without
    // dismissing again, so the sonner exit animation can finish.
    const release = () => {
      this.mirrored.delete(t.id);
      this.jobEvents.dismissToast(t.id);
    };
    const action = t.action;
    const options = {
      id: t.id,
      description: t.message,
      duration: Number.POSITIVE_INFINITY,
      important: !!t.isError,
      onDismiss: release,
      action: action
        ? {
            label: action.label,
            onClick: () => {
              this.actionRequest.emit(action);
              release();
            },
          }
        : undefined,
    };
    // Errors: octagon-x; finished jobs (they carry a next step): check; everything else: info
    if (t.isError) {
      toast.error(t.title, options);
    } else if (action) {
      toast.success(t.title, options);
    } else {
      toast.info(t.title, options);
    }
  }
}
