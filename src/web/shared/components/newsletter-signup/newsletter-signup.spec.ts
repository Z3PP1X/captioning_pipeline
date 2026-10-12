import { ComponentFixture, TestBed } from '@angular/core/testing';
import {
  NEWSLETTER_CONSENT_TEXT_VERSION,
  NewsletterSubscriptionRequest,
} from '../../../core/models/newsletter.model';
import {
  NEWSLETTER_SIGNUP_TITLE_ID,
  NewsletterSignup,
  NewsletterSignupStatus,
  newsletterConsentText,
} from './newsletter-signup';

const ORGANIZATION = 'Was tun? e.V.';

describe('NewsletterSignup', () => {
  let fixture: ComponentFixture<NewsletterSignup>;
  let host: HTMLElement;
  let emitted: NewsletterSubscriptionRequest[];
  let dismissed: number;

  beforeEach(async () => {
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

    await TestBed.configureTestingModule({ imports: [NewsletterSignup] }).compileComponents();

    fixture = TestBed.createComponent(NewsletterSignup);
    fixture.componentRef.setInput('organization', ORGANIZATION);
    host = fixture.nativeElement as HTMLElement;
    emitted = [];
    dismissed = 0;
    fixture.componentInstance.subscribe.subscribe((request) => emitted.push(request));
    fixture.componentInstance.dismiss.subscribe(() => dismissed++);
    await fixture.whenStable();
  });

  function button(label: string): HTMLButtonElement {
    const match = Array.from(host.querySelectorAll('button')).find(
      (el) => el.textContent?.trim() === label,
    );
    if (!match) throw new Error(`Button "${label}" not found`);
    return match;
  }

  function input(autocomplete: string): HTMLInputElement {
    return host.querySelector(`input[autocomplete="${autocomplete}"]`) as HTMLInputElement;
  }

  function consentCheckbox(): HTMLButtonElement {
    return host.querySelector('[role="checkbox"]') as HTMLButtonElement;
  }

  async function type(el: HTMLInputElement, value: string): Promise<void> {
    el.value = value;
    el.dispatchEvent(new Event('input'));
    el.dispatchEvent(new Event('blur'));
    await fixture.whenStable();
  }

  async function openForm(): Promise<void> {
    button('Ja, zum Newsletter').click();
    await fixture.whenStable();
  }

  async function submit(): Promise<void> {
    (host.querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
    await fixture.whenStable();
  }

  async function setStatus(status: NewsletterSignupStatus): Promise<void> {
    fixture.componentRef.setInput('status', status);
    await fixture.whenStable();
  }

  async function fillValid(email = 'anna@example.com', firstName = ''): Promise<void> {
    await type(input('email'), email);
    await type(input('given-name'), firstName);
    consentCheckbox().click();
    await fixture.whenStable();
  }

  it('starts with the voluntary question and an h2 carrying the title id', () => {
    const title = host.querySelector('h2');
    expect(title?.id).toBe(NEWSLETTER_SIGNUP_TITLE_ID);
    expect(host.textContent).toContain('Möchtest du über den Kongress informiert werden?');
    expect(host.querySelector('form')).toBeNull();
  });

  it('emits dismiss on "Nein, danke" without emitting a subscription', async () => {
    button('Nein, danke').click();
    await fixture.whenStable();

    expect(dismissed).toBe(1);
    expect(emitted).toEqual([]);
  });

  it('shows the form with an unticked consent checkbox after "Ja, zum Newsletter"', async () => {
    await openForm();

    expect(host.querySelector('form')).not.toBeNull();
    expect(input('email').type).toBe('email');
    expect(input('email').getAttribute('inputmode')).toBe('email');
    expect(input('given-name')).not.toBeNull();
    expect(host.textContent).toContain('(optional)');
    expect(consentCheckbox().getAttribute('aria-checked')).toBe('false');
    expect(host.querySelector(`label[for="${input('email').id}"]`)?.textContent).toContain(
      'E-Mail-Adresse',
    );
    expect(host.querySelector(`label[for="${consentCheckbox().id}"]`)?.textContent).toContain(
      'Ich willige ein',
    );
    expect(document.activeElement).toBe(input('email'));
    expect(host.textContent).toContain(newsletterConsentText(ORGANIZATION));
    expect(host.textContent).toContain(
      'Ich willige ein, dass der Was tun? e.V. mir per E-Mail Informationen zum Kongress',
    );
  });

  it('shows the privacy information next to the form', async () => {
    await openForm();

    const info = host.querySelector('[data-testid="newsletter-info"]') as HTMLElement;
    expect(info.textContent).toContain(ORGANIZATION);
    expect(info.textContent).toContain('nicht an Dritte');
    expect(info.textContent).toContain('widerrufen');
    const link = info.querySelector('a') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/datenschutz');
    expect(link.target).toBe('_blank');
    expect(link.rel).toContain('noopener');
    expect(link.textContent).toContain('Datenschutzerklärung');
  });

  it('blocks submit without consent and shows the consent error', async () => {
    await openForm();
    await type(input('email'), 'anna@example.com');

    await submit();

    expect(emitted).toEqual([]);
    expect(host.textContent).toContain('Bitte setze den Haken');
  });

  it('blocks submit with an invalid email and marks the input invalid', async () => {
    await openForm();
    await fillValid('keine-mail');

    await submit();

    expect(emitted).toEqual([]);
    expect(input('email').getAttribute('aria-invalid')).toBe('true');
    expect(host.textContent).toContain('gültige E-Mail-Adresse');
  });

  it('does not show errors before the user interacted', async () => {
    await openForm();

    expect(input('email').getAttribute('aria-invalid')).toBeNull();
    expect(host.textContent).not.toContain('Bitte gib deine E-Mail-Adresse ein.');
  });

  it('emits a trimmed request with consent proof and without an empty first name', async () => {
    await openForm();
    await fillValid('  anna@example.com  ', '   ');
    const before = Date.now();

    await submit();

    expect(emitted).toHaveLength(1);
    const [request] = emitted;
    expect(request.email).toBe('anna@example.com');
    expect('firstName' in request).toBe(false);
    expect(request.consent.given).toBe(true);
    expect(request.consent.textVersion).toBe(NEWSLETTER_CONSENT_TEXT_VERSION);
    expect(new Date(request.consent.timestamp).toISOString()).toBe(request.consent.timestamp);
    expect(Date.parse(request.consent.timestamp)).toBeGreaterThanOrEqual(before - 1000);
  });

  it('includes a trimmed first name when given', async () => {
    await openForm();
    await fillValid('anna@example.com', ' Anna ');

    await submit();

    expect(emitted[0].firstName).toBe('Anna');
  });

  it('disables the submit button and shows a spinner while submitting', async () => {
    await openForm();
    await fillValid();
    await setStatus('submitting');

    const submitButton = host.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);
    expect(submitButton.querySelector('hlm-spinner')).not.toBeNull();

    await submit();
    expect(emitted).toEqual([]);
  });

  it('shows a destructive alert on error, keeps the values and allows a retry', async () => {
    await openForm();
    await fillValid('anna@example.com', 'Anna');
    await submit();
    await setStatus('submitting');
    await setStatus('error');

    const alert = host.querySelector('[data-testid="newsletter-error"]') as HTMLElement;
    expect(alert).not.toBeNull();
    expect(alert.getAttribute('role')).toBe('alert');
    expect(document.activeElement).toBe(alert);
    expect(alert.querySelector('ng-icon')).not.toBeNull();
    expect(alert.textContent).toContain('Das hat leider nicht geklappt.');
    expect(alert.textContent).toContain('Bitte versuche es erneut.');
    expect(input('email').value).toBe('anna@example.com');
    expect(input('given-name').value).toBe('Anna');
    expect(consentCheckbox().getAttribute('aria-checked')).toBe('true');

    await submit();
    expect(emitted).toHaveLength(2);
  });

  it('renders the double opt-in success view and dismisses via "Schließen"', async () => {
    await openForm();
    await setStatus('success');

    const success = host.querySelector('[data-testid="newsletter-success"]') as HTMLElement;
    expect(success.textContent).toContain(
      'Fast geschafft – bitte bestätige deine Anmeldung über den Link in der E-Mail, die wir dir gerade geschickt haben.',
    );
    expect(success.textContent).not.toContain('angemeldet');
    expect(success.querySelector('h2')?.id).toBe(NEWSLETTER_SIGNUP_TITLE_ID);
    expect(document.activeElement).toBe(success.querySelector('h2'));
    expect(host.querySelector('form')).toBeNull();

    button('Schließen').click();
    expect(dismissed).toBe(1);
  });

  it('goes back to the question with "Zurück"', async () => {
    await openForm();
    button('Zurück').click();
    await fixture.whenStable();

    expect(host.querySelector('form')).toBeNull();
    expect(host.textContent).toContain('Möchtest du über den Kongress informiert werden?');
  });
});
