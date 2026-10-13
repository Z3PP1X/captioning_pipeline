import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideRefreshCw, lucidePackage } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { DatasetService } from '../../core/services/dataset.service';

@Component({
  selector: 'app-dataset-view',
  imports: [NgIcon, HlmButtonImports],
  providers: [provideIcons({ lucideRefreshCw, lucidePackage })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-6 p-4 md:p-6 overflow-y-auto h-full">
      <!-- Dataset Header -->
      <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 shadow-xs select-none">
        <div>
          <h2 class="font-display font-black text-lg md:text-xl uppercase tracking-wide text-foreground">
            Finished Dataset Overview
          </h2>
          <span class="text-xs text-muted-foreground">
            Directory: <code class="font-mono bg-muted px-1.5 py-0.5 rounded text-[11px]">{{ datasetService.metrics().finished_dir }}</code>
          </span>
        </div>

        <div class="flex items-center gap-2">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="datasetService.loadDataset()"
            [disabled]="datasetService.isLoading()"
            class="cursor-pointer gap-1.5"
          >
            <ng-icon
              name="lucideRefreshCw"
              class="size-3.5"
              [class.animate-spin]="datasetService.isLoading()"
            />
            <span>Refresh</span>
          </button>

          <button
            hlmBtn
            variant="default"
            size="sm"
            (click)="datasetService.exportAllCurated()"
            [disabled]="datasetService.isLoading()"
            class="cursor-pointer gap-1.5 font-bold"
          >
            <ng-icon name="lucidePackage" class="size-3.5" />
            <span>Export All Curated Videos</span>
          </button>
        </div>
      </div>

      <!-- KPI Metrics Cards (Signature KPI Typography) -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <!-- Card 1: Total Images -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-3xl md:text-4xl text-primary leading-none">
            {{ datasetService.metrics().total_exports }}
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Total Exported Images
          </span>
        </div>

        <!-- Card 2: Curated Videos -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-3xl md:text-4xl text-foreground leading-none">
            {{ datasetService.metrics().per_video.length }}
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Curated Videos
          </span>
        </div>

        <!-- Card 3: Image Spec -->
        <div class="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-xs">
          <span class="font-display font-black text-2xl md:text-3xl text-foreground leading-none">
            PNG 100%
          </span>
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Lossless sRGB (Snap % 16)
          </span>
        </div>
      </div>

      <!-- Exported Breakdown Table -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-xs">
        <div class="p-4 border-b border-border">
          <h3 class="font-sans font-bold text-sm text-foreground">
            Exported Videos Breakdown
          </h3>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs">
            <thead class="bg-muted text-muted-foreground font-semibold uppercase tracking-wider text-[11px] border-b border-border">
              <tr>
                <th class="p-3">Video Name</th>
                <th class="p-3 text-right">Frames Exported</th>
                <th class="p-3">Per-Video Folder</th>
                <th class="p-3">Action</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-border">
              @for (item of datasetService.metrics().per_video; track item.id) {
                <tr class="hover:bg-muted/40 transition-colors">
                  <td class="p-3 font-medium text-foreground">{{ item.name }}</td>
                  <td class="p-3 font-mono text-primary font-bold text-right">{{ item.count }}</td>
                  <td class="p-3 font-mono text-muted-foreground text-[11px]">
                    /finished/{{ item.slug }}/
                  </td>
                  <td class="p-3">
                    <button
                      hlmBtn
                      variant="outline"
                      size="xs"
                      (click)="datasetService.exportVideo(item.id)"
                      class="cursor-pointer"
                    >
                      Re-Export
                    </button>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="p-6 text-center text-muted-foreground">
                    No datasets exported yet. Curate candidate frames and click Export.
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
})
export class DatasetViewComponent {
  readonly datasetService = inject(DatasetService);
}
