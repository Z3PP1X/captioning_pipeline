import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { NgIcon } from '@ng-icons/core';
import { lucideMail } from '@ng-icons/lucide';
import { HlmButton } from '@spartan-ng/helm/button';

/**
 * Central call to action (dumb). Holds the page's single primary button.
 * Generic on purpose: the same component later becomes the ticket CTA (texts + icon change).
 */
@Component({
  selector: 'app-cta-section',
  imports: [NgIcon, HlmButton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    <section
      [id]="sectionId()"
      [attr.aria-labelledby]="sectionId() + '-title'"
      class="scroll-mt-14 px-4 py-12 md:scroll-mt-16 md:px-6 md:py-16"
    >
      <div class="mx-auto flex w-full max-w-prose flex-col items-center gap-4 text-center">
        <h2 [id]="sectionId() + '-title'" class="font-sans text-2xl font-bold text-balance md:text-3xl">
          {{ title() }}
        </h2>
        @if (text()) {
          <p class="text-muted-foreground text-base leading-relaxed">{{ text() }}</p>
        }
        <!-- Oversized on purpose: the page's hero action, ~26rem × 5rem from md. -->
        <button
          hlmBtn
          size="lg"
          type="button"
          class="mt-4 h-16 w-full gap-3 rounded-xl px-8 text-xl font-bold shadow-lg sm:w-auto sm:min-w-80 md:h-20 md:min-w-104 md:px-12 md:text-2xl"
          (click)="action.emit()"
        >
          @if (icon()) {
            <ng-icon [svg]="icon()!" class="text-2xl md:text-3xl" aria-hidden="true" />
          }
          {{ buttonLabel() }}
        </button>
      </div>
    </section>
  `,
})
export class CtaSection {
  readonly title = input.required<string>();
  readonly text = input<string>('');
  readonly buttonLabel = input.required<string>();
  /** Lucide SVG string (e.g. `lucideMail`, `lucideTicket` from `@ng-icons/lucide`); `null` hides the icon. */
  readonly icon = input<string | null>(lucideMail);
  /** Anchor id of the section (nav fragment). Defaults to the newsletter CTA. */
  readonly sectionId = input<string>('informiert-bleiben');

  readonly action = output<void>();
}
