import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCheck, lucideX, lucideAlertTriangle } from '@ng-icons/lucide';
import { JobEventService, ToastAction } from '../../core/services/job-event.service';
import { VideoService } from '../../core/services/video.service';

@Component({
  selector: 'app-toast-container',
  imports: [NgIcon],
  providers: [provideIcons({ lucideCheck, lucideX, lucideAlertTriangle })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none select-none">
      @for (toast of jobEvents.toasts(); track toast.id) {
        <div
          class="pointer-events-auto flex items-start gap-3 rounded-xl border border-border bg-card p-3 shadow-xl animate-in slide-in-from-bottom-2 text-xs"
          [class.border-destructive]="toast.isError"
          [class.border-primary]="!toast.isError"
        >
          <div
            class="flex size-6 shrink-0 items-center justify-center rounded-full"
            [class]="toast.isError ? 'bg-destructive/15 text-destructive' : 'bg-primary/15 text-primary'"
          >
            @if (toast.isError) {
              <ng-icon name="lucideAlertTriangle" class="size-3.5" />
            } @else {
              <ng-icon name="lucideCheck" class="size-3.5" />
            }
          </div>

          <div class="flex-1 flex flex-col gap-0.5">
            <span class="font-bold text-foreground">{{ toast.title }}</span>
            <span class="text-muted-foreground text-xs leading-tight">{{ toast.message }}</span>

            @if (toast.action) {
              <button
                (click)="onAction(toast.action)"
                class="mt-1 text-xs font-bold text-primary hover:underline text-left cursor-pointer"
              >
                {{ toast.action.label }}
              </button>
            }
          </div>

          <button
            (click)="jobEvents.dismissToast(toast.id)"
            class="text-muted-foreground hover:text-foreground cursor-pointer p-0.5"
            title="Dismiss notification"
          >
            <ng-icon name="lucideX" class="size-3" />
          </button>
        </div>
      }
    </div>
  `,
})
export class ToastContainerComponent {
  readonly jobEvents = inject(JobEventService);
  private readonly videoService = inject(VideoService);

  readonly actionRequest = output<ToastAction>();

  onAction(action: ToastAction): void {
    if (action.videoId) {
      this.videoService.selectVideo(String(action.videoId));
    }
    this.actionRequest.emit(action);
  }
}
