import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { FooterData } from '../../../core/models/site.model';
import { SiteFooter } from './site-footer';

const FOOTER: FooterData = {
  organization: 'Was tun? e.V.',
  street: 'Schwanthalerstraße 139',
  city: '80339 München',
  phone: '+49 89 51 09 95 - 14',
  email: 'info@was-tun.de',
  responsiblePerson: 'Patrick Fuchs',
  responsibleRole: 'V.i.S.d. DDG',
  disclaimerNote: 'Hinweise zu Inhalten.',
  legalLinks: [
    { label: 'Impressum', path: '/impressum' },
    { label: 'Datenschutz', path: '/datenschutz' },
    { label: 'Kontakt', path: '/', fragment: 'kontakt' },
  ],
};

describe('SiteFooter', () => {
  let fixture: ComponentFixture<SiteFooter>;
  let host: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SiteFooter],
      providers: [provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(SiteFooter);
    fixture.componentRef.setInput('data', FOOTER);
    await fixture.whenStable();
    host = fixture.nativeElement as HTMLElement;
  });

  it('is the #kontakt anchor with an sr-only "Fußzeile" heading', () => {
    const footer = host.querySelector('footer');
    expect(footer?.id).toBe('kontakt');
    const h2 = host.querySelector('h2');
    expect(h2?.textContent?.trim()).toBe('Fußzeile');
    expect(h2?.classList).toContain('sr-only');
    expect(Array.from(host.querySelectorAll('h3')).map((h) => h.textContent?.trim())).toEqual([
      'Organisation',
      'Kontakt',
      'Verantwortlich',
    ]);
  });

  it('renders address, responsible person and disclaimer', () => {
    const text = host.textContent ?? '';
    for (const value of [
      FOOTER.organization,
      FOOTER.street,
      FOOTER.city,
      FOOTER.responsiblePerson,
      FOOTER.responsibleRole,
      FOOTER.disclaimerNote,
    ]) {
      expect(text).toContain(value);
    }
    expect(host.querySelector('address')?.textContent).toContain(FOOTER.street);
  });

  it('renders tel: and mailto: links', () => {
    const tel = host.querySelector<HTMLAnchorElement>('a[href^="tel:"]');
    expect(tel?.getAttribute('href')).toBe('tel:+498951099514');
    expect(tel?.textContent).toContain(FOOTER.phone);

    const mail = host.querySelector<HTMLAnchorElement>('a[href^="mailto:"]');
    expect(mail?.getAttribute('href')).toBe('mailto:info@was-tun.de');
  });

  it('renders the legal links with optional fragment and the current year', () => {
    const legal = Array.from(host.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Rechtliches"] a'));
    expect(legal.map((a) => a.getAttribute('href'))).toEqual(['/impressum', '/datenschutz', '/#kontakt']);
    expect(host.textContent).toContain(`© ${new Date().getFullYear()} ${FOOTER.organization}`);
  });
});
