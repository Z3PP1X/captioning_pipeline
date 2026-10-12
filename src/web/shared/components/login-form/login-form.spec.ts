import { ComponentFixture, TestBed } from '@angular/core/testing';
import { LoginCredentials } from '../../../core/models/user.model';
import { LoginForm } from './login-form';

describe('LoginForm', () => {
  let fixture: ComponentFixture<LoginForm>;
  let host: HTMLElement;
  let emitted: LoginCredentials[];

  const query = <T extends Element>(selector: string) => host.querySelector<T>(selector);
  const emailInput = () => query<HTMLInputElement>('input[type="email"]')!;
  const passwordInput = () => query<HTMLInputElement>('input[type="password"]')!;
  const submitButton = () => query<HTMLButtonElement>('button[type="submit"]')!;
  const visibleErrors = () =>
    Array.from(host.querySelectorAll<HTMLElement>('hlm-field-error'))
      .filter((el) => !el.hidden)
      .map((el) => el.textContent?.trim());

  function type(input: HTMLInputElement, value: string): void {
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }

  async function submit(): Promise<void> {
    query<HTMLFormElement>('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
  }

  beforeEach(async () => {
    fixture = TestBed.createComponent(LoginForm);
    host = fixture.nativeElement;
    emitted = [];
    fixture.componentInstance.submitted.subscribe((value) => emitted.push(value));
    await fixture.whenStable();
  });

  it('renders the card title and labelled inputs with autocomplete hints', () => {
    expect(query('h2')?.textContent?.trim()).toBe('Anmelden');
    expect(emailInput().getAttribute('autocomplete')).toBe('username');
    expect(passwordInput().getAttribute('autocomplete')).toBe('current-password');

    const emailLabel = Array.from(host.querySelectorAll('label')).find((l) =>
      l.textContent?.includes('E-Mail'),
    );
    expect(emailLabel?.getAttribute('for')).toBe(emailInput().id);
    expect(emailInput().id).not.toBe('');
  });

  it('shows no validation errors before touch/submit', () => {
    expect(visibleErrors()).toEqual([]);
    expect(emailInput().getAttribute('data-matches-spartan-invalid')).toBeNull();
  });

  it('does not emit and shows required errors when submitted empty', async () => {
    await submit();

    expect(emitted).toEqual([]);
    expect(visibleErrors()).toEqual([
      'Bitte gib deine E-Mail-Adresse ein.',
      'Bitte gib dein Passwort ein.',
    ]);
    expect(emailInput().getAttribute('aria-invalid')).toBe('true');
    expect(passwordInput().getAttribute('aria-invalid')).toBe('true');
  });

  it('shows an email format error for an invalid address', async () => {
    type(emailInput(), 'keine-mail');
    type(passwordInput(), 'geheim');
    await submit();

    expect(emitted).toEqual([]);
    expect(visibleErrors()).toEqual(['Bitte gib eine gültige E-Mail-Adresse ein.']);
  });

  it('emits the credentials when valid', async () => {
    type(emailInput(), 'orga@was-tun.de');
    type(passwordInput(), 'kongress2027');
    await submit();

    expect(emitted).toEqual([{ email: 'orga@was-tun.de', password: 'kongress2027' }]);
    expect(visibleErrors()).toEqual([]);
  });

  it('prefills from initialValue and emits it on submit', async () => {
    fixture.componentRef.setInput('initialValue', {
      email: 'orga@was-tun.de',
      password: 'kongress2027',
    });
    await fixture.whenStable();

    expect(emailInput().value).toBe('orga@was-tun.de');
    expect(passwordInput().value).toBe('kongress2027');

    await submit();
    expect(emitted).toEqual([{ email: 'orga@was-tun.de', password: 'kongress2027' }]);
  });

  it('disables the submit button, shows a spinner and does not emit while pending', async () => {
    type(emailInput(), 'orga@was-tun.de');
    type(passwordInput(), 'kongress2027');
    fixture.componentRef.setInput('pending', true);
    await fixture.whenStable();

    expect(submitButton().disabled).toBe(true);
    expect(query('hlm-spinner')).not.toBeNull();

    await submit();
    expect(emitted).toEqual([]);
  });

  it('renders the error as a destructive alert and the dev hint as a note', async () => {
    expect(query('[data-testid="login-error"]')).toBeNull();
    expect(query('[data-testid="login-dev-hint"]')).toBeNull();

    fixture.componentRef.setInput('error', 'E-Mail oder Passwort ist falsch.');
    fixture.componentRef.setInput('devHint', 'Dev: Mock-Nutzer vorausgefüllt');
    await fixture.whenStable();

    const alert = query('[data-testid="login-error"]')!;
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toContain('E-Mail oder Passwort ist falsch.');
    expect(alert.querySelector('ng-icon')).not.toBeNull();
    expect(query('[data-testid="login-dev-hint"]')?.textContent).toContain(
      'Dev: Mock-Nutzer vorausgefüllt',
    );
  });
});
