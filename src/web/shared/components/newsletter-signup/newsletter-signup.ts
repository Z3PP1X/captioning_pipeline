import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  Injector,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { email, form, FormField, required, submit } from '@angular/forms/signals';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleAlert, lucideExternalLink, lucideMailCheck } from '@ng-icons/lucide';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmCheckboxImports } from '@spartan-ng/helm/checkbox';
import { HlmFieldImports } from '@spartan-ng/helm/field';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmSpinnerImports } from '@spartan-ng/helm/spinner';
import {
  NEWSLETTER_CONSENT_TEXT_VERSION,
  NewsletterSubscriptionRequest,
} from '../../../core/models/newsletter.model';

/** Request lifecycle, owned by the container (it calls the API). */
export type NewsletterSignupStatus = 'idle' | 'submitting' | 'success' | 'error';

/** Internal wizard step; the success view is driven by `status === 'success'`. */
export type NewsletterSignupStep = 'ask' | 'form';

interface NewsletterSignupModel {
  email: string;
  firstName: string;
  consent: boolean;
}

/** Default id of the visible `h2`; the dialog/sheet wrapper references it via `aria-labelledby`. */
export const NEWSLETTER_SIGNUP_TITLE_ID = 'newsletter-signup-title';

/**
 * Consent wording (DSGVO §6 item 3). Its version is `NEWSLETTER_CONSENT_TEXT_VERSION` –
 * bump that constant whenever this text changes.
 */
export function newsletterConsentText(organization: string): string {
  return (
    `Ich willige ein, dass der ${organization} mir per E-Mail Informationen zum Kongress ` +
    '(Programm, Tickets, Termine) zusendet. Die Einwilligung kann ich jederzeit mit Wirkung ' +
    'für die Zukunft widerrufen, z. B. über den Abmeldelink in jeder E-Mail.'
  );
}

let nextId = 0;

/**
 * Dumb DSGVO-compliant newsletter wizard (ask → form → success).
 * Content only: the container wraps it in `hlm-dialog` (md+) or a bottom `hlm-sheet`,
 * calls the API on `subscribe` and feeds the outcome back via `status`.
 */
@Component({
  selector: 'app-newsletter-signup',
  imports: [
    FormField,
    NgIcon,
    HlmAlertImports,
    HlmButtonImports,
    HlmCheckboxImports,
    HlmFieldImports,
    HlmInputImports,
    HlmSpinnerImports,
  ],
  providers: [provideIcons({ lucideCircleAlert, lucideExternalLink, lucideMailCheck })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  templateUrl: './newsletter-signup.html',
})
export class NewsletterSignup {
  private readonly injector = inject(Injector);

  readonly status = input<NewsletterSignupStatus>('idle');
  readonly privacyPolicyPath = input('/datenschutz');
  readonly organization = input.required<string>();
  readonly titleId = input(NEWSLETTER_SIGNUP_TITLE_ID);

  readonly subscribe = output<NewsletterSubscriptionRequest>();
  readonly dismiss = output<void>();

  protected readonly step = signal<NewsletterSignupStep>('ask');
  protected readonly submitting = computed(() => this.status() === 'submitting');
  protected readonly consentText = computed(() => newsletterConsentText(this.organization()));

  private readonly idPrefix = `newsletter-signup-${nextId++}`;
  protected readonly ids = {
    email: `${this.idPrefix}-email`,
    firstName: `${this.idPrefix}-first-name`,
    consent: `${this.idPrefix}-consent`,
    consentError: `${this.idPrefix}-consent-error`,
    info: `${this.idPrefix}-info`,
  };

  /** Signal Forms model; kept while the component lives (e.g. after an error for a retry). */
  private readonly model = signal<NewsletterSignupModel>({
    email: '',
    firstName: '',
    // Never pre-checked (Art. 7 GDPR): consent must be an active opt-in.
    consent: false,
  });

  protected readonly form = form(this.model, (path) => {
    required(path.email);
    email(path.email);
    // `required` treats `false` as empty, i.e. the box must be ticked.
    required(path.consent);
  });

  private readonly emailInput = viewChild<ElementRef<HTMLInputElement>>('emailInput');
  private readonly consentField = viewChild<ElementRef<HTMLElement>>('consentField');
  private readonly errorAlert = viewChild<ElementRef<HTMLElement>>('errorAlert');
  private readonly successTitle = viewChild<ElementRef<HTMLElement>>('successTitle');

  constructor() {
    // Keep keyboard/screen-reader focus inside the content when the view swaps.
    effect(() => {
      const status = this.status();
      untracked(() => {
        if (status === 'success') this.focusAfterRender(() => this.successTitle());
        if (status === 'error') this.focusAfterRender(() => this.errorAlert());
      });
    });
  }

  protected showForm(): void {
    this.step.set('form');
    this.focusAfterRender(() => this.emailInput());
  }

  protected back(): void {
    this.step.set('ask');
  }

  protected close(): void {
    this.dismiss.emit();
  }

  protected onSubmit(event: Event): void {
    event.preventDefault();
    if (this.submitting()) return;

    // `submit()` marks every field as touched first, so hlm-field-error / aria-invalid show up.
    void submit(this.form, {
      action: async () => {
        const { email, firstName } = this.model();
        const trimmedFirstName = firstName.trim();
        this.subscribe.emit({
          email: email.trim(),
          ...(trimmedFirstName ? { firstName: trimmedFirstName } : {}),
          consent: {
            given: true,
            textVersion: NEWSLETTER_CONSENT_TEXT_VERSION,
            timestamp: new Date().toISOString(),
          },
        });
        return undefined;
      },
      onInvalid: () => this.focusFirstInvalid(),
    });
  }

  private focusFirstInvalid(): void {
    if (this.form.email().invalid()) {
      this.emailInput()?.nativeElement.focus();
      return;
    }
    this.consentField()?.nativeElement.querySelector<HTMLElement>('[role="checkbox"]')?.focus();
  }

  private focusAfterRender(target: () => ElementRef<HTMLElement> | undefined): void {
    afterNextRender(() => target()?.nativeElement.focus(), { injector: this.injector });
  }
}
