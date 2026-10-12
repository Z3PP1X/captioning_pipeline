import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { HlmAccordionImports } from '@spartan-ng/helm/accordion';
import { FaqItem } from '../../../core/models/faq.model';

/**
 * FAQ block (dumb): section heading + single, collapsible hlm-accordion, seamless (no card) on a
 * white band with hairline dividers. Questions are primary-red on white (4.70:1).
 * Renders nothing when there are no items (the page decides whether to show a fallback).
 */
@Component({
  selector: 'app-faq-section',
  imports: [HlmAccordionImports],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    @if (items().length) {
      <section
        id="faq"
        aria-labelledby="faq-title"
        class="bg-background scroll-mt-14 px-4 py-12 md:scroll-mt-16 md:px-6 md:py-16 lg:px-8"
      >
        <div class="mx-auto flex w-full max-w-4xl flex-col gap-4">
          <h2 id="faq-title" class="font-sans text-2xl font-bold text-balance md:text-3xl">
            {{ title() }}
          </h2>

          <hlm-accordion type="single" class="border-t">
            @for (item of items(); track item.id) {
              <hlm-accordion-item class="border-b">
                <hlm-accordion-trigger
                  triggerClass="text-primary py-5 text-xl font-normal md:py-6 md:text-2xl **:data-[slot=accordion-trigger-icon]:text-primary! **:data-[slot=accordion-trigger-icon]:mt-1.5"
                >
                  {{ item.question }}
                </hlm-accordion-trigger>
                <hlm-accordion-content>
                  <p class="max-w-prose pb-2 text-base leading-relaxed md:text-lg">
                    {{ item.answer }}
                  </p>
                </hlm-accordion-content>
              </hlm-accordion-item>
            }
          </hlm-accordion>
        </div>
      </section>
    }
  `,
})
export class FaqSection {
  readonly title = input.required<string>();
  readonly items = input.required<FaqItem[]>();
}
