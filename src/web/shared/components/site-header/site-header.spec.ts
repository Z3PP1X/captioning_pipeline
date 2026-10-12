import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { SiteNavigation } from '../../../core/models/site.model';
import { SiteHeader } from './site-header';

const NAVIGATION: SiteNavigation = {
  brand: { label: 'Was tun?!', logoSrc: 'shared/mim26.png' },
  items: [
    { label: 'Kongress', fragment: 'kongress' },
    { label: 'Informiert bleiben', fragment: 'informiert-bleiben' },
    { label: 'FAQ', fragment: 'faq' },
  ],
  loginLabel: 'Anmelden',
};

describe('SiteHeader', () => {
  let fixture: ComponentFixture<SiteHeader>;
  let host: HTMLElement;

  beforeEach(async () => {
    // jsdom lacks matchMedia (read by CDK overlays / tooltip).
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }),
    });

    await TestBed.configureTestingModule({
      imports: [SiteHeader],
      providers: [provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(SiteHeader);
    fixture.componentRef.setInput('navigation', NAVIGATION);
    await fixture.whenStable();
    host = fixture.nativeElement as HTMLElement;
  });

  const desktopLinks = () =>
    Array.from(host.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Hauptnavigation"] a'));

  it('renders brand, logo and the nav items as anchor links on "/"', () => {
    expect(host.textContent).toContain('Was tun?!');
    expect(host.querySelector('img')?.getAttribute('src')).toBe('shared/mim26.png');

    const links = desktopLinks();
    expect(links.map((a) => a.textContent?.trim())).toEqual(['Kongress', 'Informiert bleiben', 'FAQ']);
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/#kongress',
      '/#informiert-bleiben',
      '/#faq',
    ]);
  });

  it('renders the skip link to #main-content', () => {
    const skip = Array.from(host.querySelectorAll('a')).find((a) =>
      a.textContent?.includes('Zum Inhalt springen'),
    );
    expect(skip?.getAttribute('href')).toContain('#main-content');
    expect(skip?.classList).toContain('sr-only');
  });

  it('renders the hidden login icon button pointing to /login with an sr-only label', () => {
    const login = host.querySelector<HTMLAnchorElement>('a[href="/login"]');
    expect(login).not.toBeNull();
    expect(login?.querySelector('.sr-only')?.textContent?.trim()).toBe('Anmelden');
    expect(login?.classList).toContain('text-muted-foreground');
  });

  it('emits newsletterClick only for the "informiert-bleiben" item', () => {
    let emitted = 0;
    fixture.componentInstance.newsletterClick.subscribe(() => emitted++);

    const [kongress, newsletter] = desktopLinks();
    kongress.click();
    expect(emitted).toBe(0);

    newsletter.click();
    expect(emitted).toBe(1);
  });

  it('has a burger button with an accessible label for the mobile drawer', () => {
    const burger = host.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]');
    expect(burger?.textContent).toContain('Menü öffnen');
  });

  it('opens the drawer with the nav links and closes it after a link click', async () => {
    const burger = host.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]')!;
    burger.click();
    await fixture.whenStable();
    expect(burger.getAttribute('aria-expanded')).toBe('true');

    const sheet = document.querySelector<HTMLElement>('hlm-sheet-content')!;
    const links = Array.from(sheet.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Hauptnavigation"] a'));
    expect(links.map((a) => a.textContent?.trim())).toEqual(['Kongress', 'Informiert bleiben', 'FAQ']);

    let emitted = 0;
    fixture.componentInstance.newsletterClick.subscribe(() => emitted++);
    links[1].click();
    await fixture.whenStable();

    expect(emitted).toBe(1);
    expect(burger.getAttribute('aria-expanded')).toBe('false');
  });
});
