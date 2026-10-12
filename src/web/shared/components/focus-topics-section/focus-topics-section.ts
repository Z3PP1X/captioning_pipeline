import { BreakpointObserver } from '@angular/cdk/layout';
import { NgTemplateOutlet } from '@angular/common';
import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideArrowLeft, lucideArrowRight, lucideX } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmCard } from '@spartan-ng/helm/card';
import { HlmCarouselImports } from '@spartan-ng/helm/carousel';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { HlmSheetImports } from '@spartan-ng/helm/sheet';
import type { EmblaOptionsType } from 'embla-carousel';
import { map } from 'rxjs';
import { FocusTopic } from '../../../core/models/congress.model';

/** Below `md` (768px) the detail modal is a bottom sheet, from `md` a centered dialog. */
export const FOCUS_TOPIC_SHEET_QUERY = '(max-width: 767.98px)';
export const FOCUS_TOPIC_TITLE_ID = 'schwerpunkt-detail-title';

/**
 * Focus topics of the congress (dumb): carousel of topic cards; a click on a card opens a
 * modal with the full description. Owns only UI state (which topic is open).
 */
@Component({
  selector: 'app-focus-topics-section',
  imports: [
    NgTemplateOutlet,
    NgIcon,
    HlmButtonImports,
    HlmCard,
    HlmCarouselImports,
    HlmDialogImports,
    HlmSheetImports,
  ],
  providers: [provideIcons({ lucideArrowLeft, lucideArrowRight, lucideX })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    @if (topics().length) {
      <section
        id="schwerpunkte"
        aria-labelledby="schwerpunkte-title"
        class="scroll-mt-14 px-4 py-12 md:scroll-mt-16 md:px-6 md:py-16 lg:px-8"
      >
        <div class="mx-auto flex w-full max-w-screen-xl flex-col gap-6">
          <div class="flex items-end justify-between gap-4">
            <div class="flex flex-col gap-2">
              <h2 id="schwerpunkte-title" class="font-sans text-2xl font-bold text-balance md:text-3xl">
                {{ title() }}
              </h2>
              @if (intro()) {
                <p class="text-muted-foreground max-w-prose text-base leading-relaxed">{{ intro() }}</p>
              }
            </div>
            <div class="hidden shrink-0 gap-2 sm:flex">
              <button
                hlmBtn
                variant="outline"
                size="icon"
                type="button"
                class="rounded-full"
                [disabled]="!carousel.canScrollPrev()"
                (click)="carousel.scrollPrev()"
              >
                <ng-icon name="lucideArrowLeft" aria-hidden="true" />
                <span class="sr-only">Vorheriger Schwerpunkt</span>
              </button>
              <button
                hlmBtn
                variant="outline"
                size="icon"
                type="button"
                class="rounded-full"
                [disabled]="!carousel.canScrollNext()"
                (click)="carousel.scrollNext()"
              >
                <ng-icon name="lucideArrowRight" aria-hidden="true" />
                <span class="sr-only">Nächster Schwerpunkt</span>
              </button>
            </div>
          </div>

          <hlm-carousel #carousel [options]="carouselOptions" aria-label="Schwerpunkte des Kongresses">
            <hlm-carousel-content>
              @for (topic of topics(); track topic.id; let i = $index) {
                <hlm-carousel-item class="flex basis-[85%] sm:basis-1/2 lg:basis-1/3" [attr.aria-label]="(i + 1) + ' von ' + topics().length">
                  <button
                    hlmCard
                    type="button"
                    class="hover:ring-foreground/25 focus-visible:ring-ring h-full w-full cursor-pointer gap-3 px-6 text-start transition-shadow outline-none hover:shadow-sm focus-visible:ring-3"
                    aria-haspopup="dialog"
                    (click)="open(topic)"
                  >
                    <span class="text-primary text-2xl font-bold tabular-nums" aria-hidden="true">
                      {{ (i + 1).toString().padStart(2, '0') }}
                    </span>
                    <span class="text-lg font-bold text-balance">{{ topic.title }}</span>
                    <span class="text-muted-foreground flex-1 text-base leading-relaxed">{{ topic.teaser }}</span>
                    <span class="mt-2 inline-flex items-center gap-1.5 text-sm font-medium underline underline-offset-4">
                      Mehr erfahren
                      <ng-icon name="lucideArrowRight" aria-hidden="true" />
                    </span>
                  </button>
                </hlm-carousel-item>
              }
            </hlm-carousel-content>
          </hlm-carousel>
        </div>
      </section>
    }

    <!-- Detail modal: bottom sheet below md, centered dialog from md (same content). -->
    @if (useSheet()) {
      <hlm-sheet side="bottom" [state]="modalState()" [aria-labelledby]="titleId" (closed)="close()">
        <hlm-sheet-content
          *hlmSheetPortal="let ctx"
          [showCloseButton]="false"
          class="max-h-[90dvh] overflow-y-auto rounded-t-xl pb-[env(safe-area-inset-bottom)]"
          data-testid="focus-topic-sheet"
        >
          <div class="relative px-4 pt-6 pb-6">
            <ng-container [ngTemplateOutlet]="detail" />
            <button hlmSheetClose hlmBtn variant="ghost" size="icon" class="absolute end-2 top-2">
              <ng-icon name="lucideX" class="text-xl" aria-hidden="true" />
              <span class="sr-only">Schließen</span>
            </button>
          </div>
        </hlm-sheet-content>
      </hlm-sheet>
    } @else {
      <hlm-dialog [state]="modalState()" [aria-labelledby]="titleId" (closed)="close()">
        <hlm-dialog-content
          *hlmDialogPortal="let ctx"
          [showCloseButton]="false"
          class="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl"
          data-testid="focus-topic-dialog"
        >
          <ng-container [ngTemplateOutlet]="detail" />
          <button hlmDialogClose hlmBtn variant="ghost" size="icon" class="absolute end-3 top-3">
            <ng-icon name="lucideX" class="text-xl" aria-hidden="true" />
            <span class="sr-only">Schließen</span>
          </button>
        </hlm-dialog-content>
      </hlm-dialog>
    }

    <ng-template #detail>
      @if (activeTopic(); as topic) {
        <div class="flex flex-col gap-4 pe-10">
          <p class="text-muted-foreground text-sm font-medium">{{ title() }}</p>
          <h2 [id]="titleId" class="text-2xl font-bold text-balance">{{ topic.title }}</h2>
        </div>
        <div class="mt-4 flex flex-col gap-4 text-base leading-relaxed">
          @for (paragraph of topic.description; track $index) {
            <p>{{ paragraph }}</p>
          }
        </div>
      }
    </ng-template>
  `,
})
export class FocusTopicsSection {
  private readonly breakpoints = inject(BreakpointObserver);
  private readonly destroyRef = inject(DestroyRef);

  readonly title = input.required<string>();
  readonly intro = input<string>('');
  readonly topics = input.required<FocusTopic[]>();

  protected readonly carouselOptions: Omit<EmblaOptionsType, 'axis'> = { align: 'start' };
  protected readonly titleId = FOCUS_TOPIC_TITLE_ID;

  /** Kept after closing so the content does not vanish during the close animation. */
  protected readonly activeTopic = signal<FocusTopic | null>(null);
  private readonly modalOpen = signal(false);
  protected readonly modalState = computed(() => (this.modalOpen() ? 'open' : 'closed'));

  /** SSR and first client render use the dialog; the breakpoint is evaluated in the browser only. */
  protected readonly useSheet = signal(false);

  constructor() {
    afterNextRender(() => {
      this.breakpoints
        .observe(FOCUS_TOPIC_SHEET_QUERY)
        .pipe(
          map((state) => state.matches),
          takeUntilDestroyed(this.destroyRef),
        )
        .subscribe((matches) => this.useSheet.set(matches));
    });
  }

  protected open(topic: FocusTopic): void {
    this.activeTopic.set(topic);
    this.modalOpen.set(true);
  }

  protected close(): void {
    this.modalOpen.set(false);
  }
}
