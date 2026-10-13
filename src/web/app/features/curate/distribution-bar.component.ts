import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CurateService } from '../../core/services/curate.service';

@Component({
  selector: 'app-distribution-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 rounded-xl border border-border bg-card p-3 shadow-xs select-none">
      <!-- Stat 1: Multiple People -->
      <div class="flex flex-col gap-1 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground">👥 Multiple People</span>
          <span class="font-bold text-primary">{{ dist()?.multiple_people?.pct || 0 }}%</span>
        </div>
        <div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            class="h-full bg-primary transition-all duration-300"
            [style.width.%]="dist()?.multiple_people?.pct || 0"
          ></div>
        </div>
        <span class="text-[10px] text-muted-foreground">(Target: 50%)</span>
      </div>

      <!-- Stat 2: One Person -->
      <div class="flex flex-col gap-1 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground">👤 One Person</span>
          <span class="font-bold text-primary">{{ dist()?.one_person?.pct || 0 }}%</span>
        </div>
        <div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            class="h-full bg-primary transition-all duration-300"
            [style.width.%]="dist()?.one_person?.pct || 0"
          ></div>
        </div>
        <span class="text-[10px] text-muted-foreground">(Target: 25–30%)</span>
      </div>

      <!-- Stat 3: Tools & Equipment -->
      <div class="flex flex-col gap-1 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground">⛓️ Tools & Equip</span>
          <span class="font-bold text-primary">{{ dist()?.tools_equipment?.pct || 0 }}%</span>
        </div>
        <div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            class="h-full bg-primary transition-all duration-300"
            [style.width.%]="dist()?.tools_equipment?.pct || 0"
          ></div>
        </div>
        <span class="text-[10px] text-muted-foreground">(Target: 20–25%)</span>
      </div>

      <!-- Stat 4: Face Visible -->
      <div class="flex flex-col gap-1 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground">👁️ Face ≥50%</span>
          <span class="font-bold text-primary">{{ dist()?.face_visible?.pct || 0 }}%</span>
        </div>
        <div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            class="h-full bg-primary transition-all duration-300"
            [style.width.%]="dist()?.face_visible?.pct || 0"
          ></div>
        </div>
        <span class="text-[10px] text-muted-foreground">(Prioritized)</span>
      </div>

      <!-- Stat 5: Curated Set Total Count -->
      <div class="col-span-2 sm:col-span-1 flex flex-col justify-center items-center p-2 rounded-lg bg-primary/10 border border-primary/20 text-center">
        <span class="text-[11px] font-semibold text-foreground uppercase tracking-wide">Curated Set</span>
        <div class="flex items-baseline gap-1">
          <span class="font-display font-black text-xl text-primary leading-tight">
            {{ dist()?.selected_count || 0 }}
          </span>
          <span class="text-xs text-muted-foreground">/ 300</span>
        </div>
        @if ((dist()?.selected_count || 0) > 300) {
          <span class="text-[10px] text-primary font-semibold mt-0.5">
            +{{ (dist()?.selected_count || 0) - 300 }} over budget
          </span>
        }
      </div>
    </div>
  `,
})
export class DistributionBarComponent {
  private readonly curateService = inject(CurateService);
  readonly dist = this.curateService.distribution;
}
