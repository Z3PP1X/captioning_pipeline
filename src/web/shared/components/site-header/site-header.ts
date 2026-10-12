import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideLogIn, lucideMenu, lucideX } from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmSheetImports } from '@spartan-ng/helm/sheet';
import { HlmTooltipImports } from '@spartan-ng/helm/tooltip';

import { NavItem, SiteNavigation } from '../../../core/models/site.model';

/** Fragment of the nav item that (additionally to scrolling) opens the newsletter modal. */
export const NEWSLETTER_FRAGMENT = 'informiert-bleiben';

/**
 * Sticky site header (dumb): brand, section anchor nav (desktop links / mobile drawer)
 * and the low-key login icon button.
 */
@Component({
  selector: 'app-site-header',
  imports: [RouterLink, NgIcon, HlmButtonImports, HlmSheetImports, HlmTooltipImports],
  providers: [provideIcons({ lucideLogIn, lucideMenu, lucideX })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let nav = navigation();

    <header class="sticky top-0 z-40 border-b bg-background">
      <a
        hlmBtn
        variant="outline"
        [routerLink]="[]"
        fragment="main-content"
        queryParamsHandling="preserve"
        class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-50"
      >
        Zum Inhalt springen
      </a>

      <div class="mx-auto flex h-14 w-full max-w-screen-xl items-center gap-2 px-4 md:h-16 md:gap-4 md:px-6 lg:px-8">
        <!-- Mobile: burger → drawer -->
        <hlm-sheet side="left">
          <button hlmSheetTrigger hlmBtn variant="ghost" size="icon" class="-ml-2 md:hidden">
            <ng-icon name="lucideMenu" class="text-xl" aria-hidden="true" />
            <span class="sr-only">Menü öffnen</span>
          </button>

          <hlm-sheet-content *hlmSheetPortal="let ctx" [showCloseButton]="false" class="pb-[env(safe-area-inset-bottom)]">
            <hlm-sheet-header class="flex-row items-center justify-between border-b">
              <h2 hlmSheetTitle class="text-lg font-bold">Menü</h2>
              <button hlmSheetClose hlmBtn variant="ghost" size="icon" class="-mr-2">
                <ng-icon name="lucideX" class="text-xl" aria-hidden="true" />
                <span class="sr-only">Menü schließen</span>
              </button>
            </hlm-sheet-header>

            <nav aria-label="Hauptnavigation" class="px-2">
              <ul class="flex flex-col gap-1">
                @for (item of nav.items; track item.fragment) {
                  <li>
                    <a
                      hlmBtn
                      variant="ghost"
                      routerLink="/"
                      [fragment]="item.fragment"
                      class="w-full justify-start text-base"
                      (click)="onItemClick(item); ctx.close()"
                    >
                      {{ item.label }}
                    </a>
                  </li>
                }
              </ul>
            </nav>
          </hlm-sheet-content>
        </hlm-sheet>

        <!-- Brand -->
        <a
          routerLink="/"
          class="flex min-h-11 shrink-0 items-center gap-2 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-0"
        >
          <img
            [src]="nav.brand.logoSrc"
            alt=""
            width="2064"
            height="1032"
            class="h-8 w-auto md:h-10"
          />
          <span class="text-base font-bold md:text-lg">{{ nav.brand.label }}</span>
        </a>

        <!-- Desktop nav -->
        <nav aria-label="Hauptnavigation" class="ml-auto hidden md:block">
          <ul class="flex items-center gap-1">
            @for (item of nav.items; track item.fragment) {
              <li>
                <a
                  hlmBtn
                  variant="ghost"
                  routerLink="/"
                  [fragment]="item.fragment"
                  (click)="onItemClick(item)"
                >
                  {{ item.label }}
                </a>
              </li>
            }
          </ul>
        </nav>

        <!-- Hidden login -->
        <a
          hlmBtn
          variant="ghost"
          size="icon"
          routerLink="/login"
          [hlmTooltip]="nav.loginLabel"
          class="ml-auto -mr-2 text-muted-foreground md:ml-0 md:mr-0"
        >
          <ng-icon name="lucideLogIn" class="text-xl" aria-hidden="true" />
          <span class="sr-only">{{ nav.loginLabel }}</span>
        </a>
      </div>
    </header>
  `,
})
export class SiteHeader {
  readonly navigation = input.required<SiteNavigation>();

  /** Emitted when the "Informiert bleiben" nav item is clicked (navigation to the anchor still happens). */
  readonly newsletterClick = output<void>();

  protected onItemClick(item: NavItem): void {
    if (item.fragment === NEWSLETTER_FRAGMENT) {
      this.newsletterClick.emit();
    }
  }
}
