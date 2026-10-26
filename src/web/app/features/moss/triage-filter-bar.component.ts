import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  input,
  model,
  output,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideAlertCircle,
  lucideAlertTriangle,
  lucideArrowLeft,
  lucideArrowRight,
  lucideCheck,
  lucideCheckCircle2,
  lucideCrop,
  lucideFilter,
  lucideLayers,
  lucideListFilter,
  lucideRefreshCw,
  lucideShieldAlert,
} from '@ng-icons/lucide';
import { TriageFilter } from '../../core/models/moss.model';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';

export interface TriageTabItem {
  id: TriageFilter;
  label: string;
  icon: string;
}

export const TRIAGE_TABS: TriageTabItem[] = [
  { id: 'all', label: 'Alle', icon: 'lucideListFilter' },
  { id: 'leakage', label: 'Leakage erkannt', icon: 'lucideAlertCircle' },
  { id: 'watermark', label: 'Wasserzeichen/Ränder', icon: 'lucideShieldAlert' },
  { id: 'fallback', label: 'Fallback genutzt', icon: 'lucideRefreshCw' },
  { id: 'approved', label: 'Freigegeben', icon: 'lucideCheckCircle2' },
];

@Component({
  selector: 'app-triage-filter-bar',
  imports: [NgIcon, HlmBadgeImports, HlmButtonImports],
  providers: [
    provideIcons({
      lucideFilter,
      lucideListFilter,
      lucideAlertCircle,
      lucideShieldAlert,
      lucideRefreshCw,
      lucideCheckCircle2,
      lucideArrowLeft,
      lucideArrowRight,
      lucideCheck,
      lucideCrop,
      lucideLayers,
      lucideAlertTriangle,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col gap-2 rounded-xl border border-border bg-card p-3 text-card-foreground shadow-sm">
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <!-- Filter Tabs -->
        <div
          role="tablist"
          aria-label="Triage Filterleiste"
          class="inline-flex flex-wrap items-center gap-1 rounded-lg border border-border bg-canvas/70 p-1"
        >
          @for (tab of tabs; track tab.id) {
            <button
              type="button"
              role="tab"
              [attr.aria-selected]="activeFilter() === tab.id"
              (click)="selectFilter(tab.id)"
              class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all outline-none"
              [class.bg-card]="activeFilter() === tab.id"
              [class.text-foreground]="activeFilter() === tab.id"
              [class.shadow-xs]="activeFilter() === tab.id"
              [class.border]="activeFilter() === tab.id"
              [class.border-border]="activeFilter() === tab.id"
              [class.text-muted-foreground]="activeFilter() !== tab.id"
              [class.hover:text-foreground]="activeFilter() !== tab.id"
            >
              <ng-icon
                [name]="tab.icon"
                class="size-3.5"
                [class.text-destructive]="tab.id === 'leakage' && countFor(tab.id) > 0"
                [class.text-amber-500]="tab.id === 'watermark' && countFor(tab.id) > 0"
                [class.text-emerald-500]="tab.id === 'approved' && countFor(tab.id) > 0"
              />
              <span>{{ tab.label }}</span>
              <span
                class="rounded-full px-1.5 py-0.2 text-[10px] font-mono"
                [class.bg-muted]="activeFilter() !== tab.id"
                [class.text-muted-foreground]="activeFilter() !== tab.id"
                [class.bg-primary/10]="activeFilter() === tab.id"
                [class.text-primary]="activeFilter() === tab.id"
              >
                {{ countFor(tab.id) }}
              </span>
            </button>
          }
        </div>

        <!-- Quick Triage Navigation Action Buttons -->
        <div class="flex items-center gap-1.5 self-start lg:self-auto">
          <button
            type="button"
            hlmButton
            variant="outline"
            size="sm"
            class="h-8 px-2.5 text-xs gap-1"
            (click)="onNavigatePrevious()"
            title="Vorheriges Bild [Taste: K]"
          >
            <ng-icon name="lucideArrowLeft" class="size-3.5" />
            <kbd class="font-mono text-[10px] text-muted-foreground">[K]</kbd>
          </button>

          <button
            type="button"
            hlmButton
            variant="outline"
            size="sm"
            class="h-8 px-2.5 text-xs gap-1"
            (click)="onNavigateNext()"
            title="Nächstes Bild [Taste: J]"
          >
            <kbd class="font-mono text-[10px] text-muted-foreground">[J]</kbd>
            <ng-icon name="lucideArrowRight" class="size-3.5" />
          </button>

          <button
            type="button"
            hlmButton
            variant="secondary"
            size="sm"
            class="h-8 px-2.5 text-xs gap-1.5 text-amber-500 hover:text-amber-600 hover:bg-amber-500/10"
            (click)="onRegenerateFallback()"
            title="Mit Fallback-Modell neu generieren [Taste: R]"
          >
            <ng-icon name="lucideRefreshCw" class="size-3.5" />
            <span>[R] Regenerate</span>
          </button>

          <button
            type="button"
            hlmButton
            size="sm"
            class="h-8 px-3 text-xs gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold"
            (click)="onApproveAndAdvance()"
            title="Freigeben & Weiter [Taste: Enter]"
          >
            <ng-icon name="lucideCheck" class="size-3.5" />
            <span>[Enter] Freigeben &amp; Weiter</span>
          </button>
        </div>
      </div>

      <!-- Shortcuts Legend -->
      <div class="flex flex-wrap items-center justify-between border-t border-border/50 pt-2 text-[11px] text-muted-foreground font-mono">
        <div class="flex items-center gap-3">
          <span>Shortcuts:</span>
          <span><kbd class="rounded border px-1 bg-muted">J</kbd>/<kbd class="rounded border px-1 bg-muted">K</kbd> Navigieren</span>
          <span><kbd class="rounded border px-1 bg-muted">Enter</kbd> Freigabe</span>
          <span><kbd class="rounded border px-1 bg-muted">R</kbd> Fallback</span>
          <span><kbd class="rounded border px-1 bg-muted">C</kbd> Crop</span>
          <span><kbd class="rounded border px-1 bg-muted">Shift+C</kbd> Crop alle</span>
        </div>

        @if (activeFilter() !== 'all') {
          <span class="text-primary font-sans font-medium">
            Filter aktiv: {{ activeFilterLabel() }}
          </span>
        }
      </div>
    </div>
  `,
})
export class TriageFilterBarComponent {
  readonly tabs = TRIAGE_TABS;

  readonly activeFilter = model<TriageFilter>('all');

  // Filter counts
  readonly allCount = input<number>(0);
  readonly leakageCount = input<number>(0);
  readonly watermarkCount = input<number>(0);
  readonly fallbackCount = input<number>(0);
  readonly approvedCount = input<number>(0);

  // Keyboard navigation & triage outputs
  readonly filterChange = output<TriageFilter>();
  readonly navigateNext = output<void>();
  readonly navigatePrevious = output<void>();
  readonly approveAndAdvance = output<void>();
  readonly regenerateFallback = output<void>();
  readonly applyCrop = output<void>();
  readonly applyCropToAll = output<void>();

  countFor(tabId: TriageFilter): number {
    switch (tabId) {
      case 'all':
        return this.allCount();
      case 'leakage':
        return this.leakageCount();
      case 'watermark':
        return this.watermarkCount();
      case 'fallback':
        return this.fallbackCount();
      case 'approved':
        return this.approvedCount();
    }
  }

  activeFilterLabel(): string {
    const found = TRIAGE_TABS.find((t) => t.id === this.activeFilter());
    return found ? found.label : 'Alle';
  }

  selectFilter(tabId: TriageFilter): void {
    this.activeFilter.set(tabId);
    this.filterChange.emit(tabId);
  }

  onNavigateNext(): void {
    this.navigateNext.emit();
  }

  onNavigatePrevious(): void {
    this.navigatePrevious.emit();
  }

  onApproveAndAdvance(): void {
    this.approveAndAdvance.emit();
  }

  onRegenerateFallback(): void {
    this.regenerateFallback.emit();
  }

  @HostListener('window:keydown', ['$event'])
  handleKeyboardShortcut(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
      return;
    }

    if (event.key === 'j' || event.key === 'J') {
      event.preventDefault();
      this.onNavigateNext();
    } else if (event.key === 'k' || event.key === 'K') {
      event.preventDefault();
      this.onNavigatePrevious();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.onApproveAndAdvance();
    } else if (event.key === 'r' || event.key === 'R') {
      event.preventDefault();
      this.onRegenerateFallback();
    } else if (event.key === 'c' || event.key === 'C') {
      if (event.shiftKey) {
        event.preventDefault();
        this.applyCropToAll.emit();
      } else {
        event.preventDefault();
        this.applyCrop.emit();
      }
    }
  }
}
