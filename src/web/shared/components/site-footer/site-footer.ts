import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideMail, lucidePhone } from '@ng-icons/lucide';
import { HlmSeparatorImports } from '@spartan-ng/helm/separator';

import { FooterData } from '../../../core/models/site.model';

/** Site footer (dumb) on the ink surface; also the `#kontakt` anchor target. */
@Component({
  selector: 'app-site-footer',
  imports: [RouterLink, NgIcon, HlmSeparatorImports],
  providers: [provideIcons({ lucideMail, lucidePhone })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let footer = data();

    <footer
      id="kontakt"
      aria-labelledby="site-footer-heading"
      class="scroll-mt-14 bg-secondary text-secondary-foreground md:scroll-mt-16"
    >
      <h2 id="site-footer-heading" class="sr-only">Fußzeile</h2>

      <div class="mx-auto flex w-full max-w-screen-xl flex-col gap-8 px-4 py-10 md:px-6 md:py-12 lg:px-8">
        <div class="grid gap-8 md:grid-cols-3 md:gap-6">
          <section aria-labelledby="site-footer-org">
            <h3 id="site-footer-org" class="text-xs font-semibold uppercase tracking-wider text-secondary-foreground/70">
              Organisation
            </h3>
            <address class="mt-3 text-sm not-italic leading-relaxed md:text-base">
              <span class="block font-semibold">{{ footer.organization }}</span>
              <span class="block">{{ footer.street }}</span>
              <span class="block">{{ footer.city }}</span>
            </address>
          </section>

          <section aria-labelledby="site-footer-contact">
            <h3 id="site-footer-contact" class="text-xs font-semibold uppercase tracking-wider text-secondary-foreground/70">
              Kontakt
            </h3>
            <address class="mt-2 not-italic">
              <ul class="flex flex-col text-sm md:gap-1 md:text-base">
                <li>
                  <a
                    [href]="phoneHref()"
                    class="inline-flex min-h-11 items-center gap-2 rounded-sm underline decoration-secondary-foreground/40 underline-offset-4 outline-none transition-colors duration-150 hover:decoration-secondary-foreground focus-visible:ring-3 focus-visible:ring-ring md:min-h-0"
                  >
                    <ng-icon name="lucidePhone" class="shrink-0 text-base" aria-hidden="true" />
                    <span><span class="sr-only">Telefon: </span>{{ footer.phone }}</span>
                  </a>
                </li>
                <li>
                  <a
                    [href]="mailHref()"
                    class="inline-flex min-h-11 items-center gap-2 rounded-sm underline decoration-secondary-foreground/40 underline-offset-4 outline-none transition-colors duration-150 hover:decoration-secondary-foreground focus-visible:ring-3 focus-visible:ring-ring md:min-h-0"
                  >
                    <ng-icon name="lucideMail" class="shrink-0 text-base" aria-hidden="true" />
                    <span><span class="sr-only">E-Mail: </span>{{ footer.email }}</span>
                  </a>
                </li>
              </ul>
            </address>
          </section>

          <section aria-labelledby="site-footer-responsible">
            <h3
              id="site-footer-responsible"
              class="text-xs font-semibold uppercase tracking-wider text-secondary-foreground/70"
            >
              Verantwortlich
            </h3>
            <div class="mt-3 flex flex-col gap-2 text-sm leading-relaxed md:text-base">
              <p>
                <span class="block font-semibold">{{ footer.responsiblePerson }}</span>
                <span class="block text-secondary-foreground/80">{{ footer.responsibleRole }}</span>
              </p>
              <p class="text-sm text-secondary-foreground/80">{{ footer.disclaimerNote }}</p>
            </div>
          </section>
        </div>

        <hlm-separator decorative class="bg-secondary-foreground/20" />

        <div class="flex flex-col-reverse gap-4 text-sm md:flex-row md:items-center md:justify-between">
          <p class="text-secondary-foreground/80">© {{ currentYear }} {{ footer.organization }}</p>

          <nav aria-label="Rechtliches">
            <ul class="flex flex-wrap items-center gap-x-3">
              @for (link of footer.legalLinks; track link.label; let last = $last) {
                <li class="flex items-center gap-x-3">
                  <a
                    [routerLink]="link.path"
                    [fragment]="link.fragment"
                    class="inline-flex min-h-11 items-center rounded-sm underline decoration-secondary-foreground/40 underline-offset-4 outline-none transition-colors duration-150 hover:decoration-secondary-foreground focus-visible:ring-3 focus-visible:ring-ring md:min-h-0"
                  >
                    {{ link.label }}
                  </a>
                  @if (!last) {
                    <hlm-separator orientation="vertical" decorative class="h-4 bg-secondary-foreground/30 data-vertical:self-center" />
                  }
                </li>
              }
            </ul>
          </nav>
        </div>
      </div>
    </footer>
  `,
})
export class SiteFooter {
  readonly data = input.required<FooterData>();

  protected readonly currentYear = new Date().getFullYear();

  /** `tel:` URI without spaces, dashes or other separators (keeps a leading `+`). */
  protected readonly phoneHref = computed(() => `tel:${this.data().phone.replace(/[^\d+]/g, '')}`);

  protected readonly mailHref = computed(() => `mailto:${this.data().email}`);
}
