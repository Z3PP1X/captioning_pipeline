import { NgOptimizedImage } from '@angular/common';
import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCalendar, lucideMapPin } from '@ng-icons/lucide';
import { HlmAspectRatio } from '@spartan-ng/helm/aspect-ratio';
import { ImageAsset } from '../../../core/models/congress.model';

/**
 * Mainframe banner of the landing page (dumb).
 *
 * Renders the page's only `h1`. All text on `bg-brand` is large (h1 ≥ text-3xl,
 * tagline/meta text-xl bold ≥ 18.66px) because white on brand is AA for large text only.
 */
@Component({
  selector: 'app-hero-banner',
  imports: [NgOptimizedImage, NgIcon, HlmAspectRatio],
  providers: [provideIcons({ lucideCalendar, lucideMapPin })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    <section
      id="kongress"
      aria-labelledby="kongress-title"
      class="bg-brand text-primary-foreground scroll-mt-14 md:scroll-mt-16"
    >
      <div
        class="mx-auto flex w-full max-w-screen-xl flex-col gap-6 px-4 py-8 md:gap-8 md:px-6 md:py-12 lg:px-8"
      >
        <div class="flex flex-col gap-4">
          <h1
            id="kongress-title"
            class="font-display text-3xl leading-none font-black tracking-wide text-balance uppercase sm:text-4xl md:text-5xl"
          >
            {{ title() }}
          </h1>

          @if (tagline()) {
            <p class="max-w-prose text-xl font-bold text-balance md:text-2xl">{{ tagline() }}</p>
          }

          @if (dateLabel() || location()) {
            <ul
              class="flex flex-col gap-2 text-xl font-bold sm:flex-row sm:flex-wrap sm:gap-x-8"
              aria-label="Termin und Ort"
            >
              @if (dateLabel()) {
                <li class="flex items-center gap-2">
                  <ng-icon name="lucideCalendar" size="20" aria-hidden="true" />
                  <span class="sr-only">Termin:</span>
                  <span>{{ dateLabel() }}</span>
                </li>
              }
              @if (location()) {
                <li class="flex items-center gap-2">
                  <ng-icon name="lucideMapPin" size="20" aria-hidden="true" />
                  <span class="sr-only">Ort:</span>
                  <span>{{ location() }}</span>
                </li>
              }
            </ul>
          }
        </div>

        <div
          hlmAspectRatio="1/1"
          class="overflow-hidden rounded-xl md:aspect-21/9"
          data-testid="hero-poster"
        >
          <img [ngSrc]="poster().src" [alt]="poster().alt" fill priority class="object-cover" />
        </div>
      </div>
    </section>
  `,
})
export class HeroBanner {
  readonly poster = input.required<ImageAsset>();
  readonly title = input.required<string>();
  readonly tagline = input<string>('');
  readonly dateLabel = input<string>('');
  readonly location = input<string>('');
}
