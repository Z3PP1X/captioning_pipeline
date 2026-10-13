import { ChangeDetectionStrategy, Component, output } from '@angular/core';
import { DistributionBarComponent } from './distribution-bar.component';
import { CurateToolbarComponent } from './curate-toolbar.component';
import { CandidateGridComponent } from './candidate-grid.component';

@Component({
  selector: 'app-curate-view',
  imports: [
    DistributionBarComponent,
    CurateToolbarComponent,
    CandidateGridComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'flex-1 min-h-0 flex flex-col overflow-hidden w-full h-full',
  },
  template: `
    <div class="flex flex-col gap-3 p-3 md:p-5 overflow-hidden h-full flex-1 min-h-0">
      <!-- Top Distribution & Quota Progress Bar -->
      <app-distribution-bar class="shrink-0" />

      <!-- Curation Filters & Action Toolbar -->
      <app-curate-toolbar class="shrink-0" (openVlmModal)="openVlmModal.emit()" />

      <!-- Scrollable Candidate Frames Grid containered by Chunks -->
      <app-candidate-grid
        class="flex-1 min-h-0 flex flex-col overflow-hidden w-full"
        (seekChunk)="seekChunk.emit($event)"
      />
    </div>
  `,
})
export class CurateViewComponent {
  readonly openVlmModal = output<void>();
  readonly seekChunk = output<number>();
}
