import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideUsers, lucideUser, lucideWrench, lucideEye, lucideAlertTriangle } from '@ng-icons/lucide';
import { HlmProgressImports } from '@spartan-ng/helm/progress';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { CurateService } from '../../core/services/curate.service';

@Component({
  selector: 'app-distribution-bar',
  imports: [NgIcon, HlmProgressImports, HlmBadgeImports],
  providers: [provideIcons({ lucideUsers, lucideUser, lucideWrench, lucideEye, lucideAlertTriangle })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 rounded-xl border border-border bg-card p-3 shadow-xs select-none">
      <!-- Stat 1: Multiple People -->
      <div class="flex flex-col gap-1.5 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground flex items-center gap-1.5">
            <ng-icon name="lucideUsers" class="size-3.5 text-muted-foreground" />
            <span>Multiple People</span>
          </span>
          <div class="flex items-center gap-1">
            @if (multipleHealth() === 'warning' || multipleHealth() === 'danger') {
              <ng-icon name="lucideAlertTriangle" class="size-3 text-warning" title="Outside optimal range" />
            }
            <span class="font-bold text-foreground tabular-nums">{{ dist()?.multiple_people?.pct || 0 }}%</span>
          </div>
        </div>
        <hlm-progress [value]="dist()?.multiple_people?.pct || 0" class="h-1.5">
          <hlm-progress-indicator [class]="multipleHealth() === 'good' ? 'bg-success' : multipleHealth() === 'warning' ? 'bg-warning' : 'bg-primary'" />
        </hlm-progress>
        <span class="text-xs text-muted-foreground">(Target: ~50%)</span>
      </div>

      <!-- Stat 2: One Person -->
      <div class="flex flex-col gap-1.5 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground flex items-center gap-1.5">
            <ng-icon name="lucideUser" class="size-3.5 text-muted-foreground" />
            <span>One Person</span>
          </span>
          <div class="flex items-center gap-1">
            @if (onePersonHealth() === 'warning') {
              <ng-icon name="lucideAlertTriangle" class="size-3 text-warning" title="Outside optimal range" />
            }
            <span class="font-bold text-foreground tabular-nums">{{ dist()?.one_person?.pct || 0 }}%</span>
          </div>
        </div>
        <hlm-progress [value]="dist()?.one_person?.pct || 0" class="h-1.5">
          <hlm-progress-indicator [class]="onePersonHealth() === 'good' ? 'bg-success' : onePersonHealth() === 'warning' ? 'bg-warning' : 'bg-primary'" />
        </hlm-progress>
        <span class="text-xs text-muted-foreground">(Target: 25–30%)</span>
      </div>

      <!-- Stat 3: Tools & Equipment -->
      <div class="flex flex-col gap-1.5 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground flex items-center gap-1.5">
            <ng-icon name="lucideWrench" class="size-3.5 text-muted-foreground" />
            <span>Tools & Equip</span>
          </span>
          <div class="flex items-center gap-1">
            @if (toolsHealth() === 'warning') {
              <ng-icon name="lucideAlertTriangle" class="size-3 text-warning" title="Outside optimal range" />
            }
            <span class="font-bold text-foreground tabular-nums">{{ dist()?.tools_equipment?.pct || 0 }}%</span>
          </div>
        </div>
        <hlm-progress [value]="dist()?.tools_equipment?.pct || 0" class="h-1.5">
          <hlm-progress-indicator [class]="toolsHealth() === 'good' ? 'bg-success' : toolsHealth() === 'warning' ? 'bg-warning' : 'bg-primary'" />
        </hlm-progress>
        <span class="text-xs text-muted-foreground">(Target: 20–25%)</span>
      </div>

      <!-- Stat 4: Face Visible -->
      <div class="flex flex-col gap-1.5 p-2 rounded-lg bg-background border border-border/50">
        <div class="flex items-center justify-between text-xs">
          <span class="font-medium text-foreground flex items-center gap-1.5">
            <ng-icon name="lucideEye" class="size-3.5 text-muted-foreground" />
            <span>Face ≥50%</span>
          </span>
          <span class="font-bold text-foreground tabular-nums">{{ dist()?.face_visible?.pct || 0 }}%</span>
        </div>
        <hlm-progress [value]="dist()?.face_visible?.pct || 0" class="h-1.5">
          <hlm-progress-indicator class="bg-primary" />
        </hlm-progress>
        <span class="text-xs text-muted-foreground">(Prioritized)</span>
      </div>

      <!-- Stat 5: Curated Set Total Count -->
      <div class="col-span-2 sm:col-span-1 flex flex-col justify-center items-center p-2 rounded-lg bg-primary/10 border border-primary/20 text-center">
        <span class="text-xs font-semibold text-foreground uppercase tracking-wide">Curated Set</span>
        <div class="flex items-baseline gap-1">
          <span class="font-display font-black text-xl text-primary leading-tight tabular-nums">
            {{ dist()?.selected_count || 0 }}
          </span>
          <span class="text-xs text-muted-foreground tabular-nums">/ 300</span>
        </div>
        @if ((dist()?.selected_count || 0) > 300) {
          <span class="text-xs text-destructive font-semibold mt-0.5 tabular-nums">
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

  readonly multipleHealth = computed(() => {
    const pct = this.dist()?.multiple_people?.pct ?? 0;
    if (pct === 0) return 'neutral';
    if (pct >= 45 && pct <= 55) return 'good';
    if (pct >= 35 && pct <= 65) return 'warning';
    return 'danger';
  });

  readonly onePersonHealth = computed(() => {
    const pct = this.dist()?.one_person?.pct ?? 0;
    if (pct === 0) return 'neutral';
    if (pct >= 20 && pct <= 35) return 'good';
    return 'warning';
  });

  readonly toolsHealth = computed(() => {
    const pct = this.dist()?.tools_equipment?.pct ?? 0;
    if (pct === 0) return 'neutral';
    if (pct >= 15 && pct <= 30) return 'good';
    return 'warning';
  });
}
