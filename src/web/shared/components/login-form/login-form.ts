import {
  ChangeDetectionStrategy,
  Component,
  effect,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { email, form, FormField, required, submit } from '@angular/forms/signals';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleAlert, lucideInfo } from '@ng-icons/lucide';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmCardImports } from '@spartan-ng/helm/card';
import { HlmFieldImports } from '@spartan-ng/helm/field';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmSpinnerImports } from '@spartan-ng/helm/spinner';
import { LoginCredentials } from '../../../core/models/user.model';

/**
 * Dumb login form: renders the credentials form inside a card and emits valid credentials.
 * Pending/error state and any prefill come from the container.
 */
@Component({
  selector: 'app-login-form',
  imports: [
    FormField,
    NgIcon,
    HlmAlertImports,
    HlmBadgeImports,
    HlmButtonImports,
    HlmCardImports,
    HlmFieldImports,
    HlmInputImports,
    HlmSpinnerImports,
  ],
  providers: [provideIcons({ lucideCircleAlert, lucideInfo })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block w-full' },
  template: `
    <section hlmCard aria-labelledby="login-form-title">
      <div hlmCardHeader>
        <h2 hlmCardTitle id="login-form-title" class="text-lg font-bold">Anmelden</h2>
        <p hlmCardDescription>Melde dich mit deinem Orga-Zugang an.</p>
        @if (devHint(); as hint) {
          <span hlmBadge variant="muted" class="mt-2" data-testid="login-dev-hint">
            <ng-icon name="lucideInfo" data-icon="inline-start" aria-hidden="true" />
            {{ hint }}
          </span>
        }
      </div>

      <form
        hlmCardContent
        class="flex flex-col gap-6"
        novalidate
        [attr.aria-busy]="pending() || null"
        (submit)="onSubmit($event)"
      >
        @if (error(); as message) {
          <div hlmAlert variant="destructive" data-testid="login-error">
            <ng-icon name="lucideCircleAlert" aria-hidden="true" />
            <p hlmAlertTitle>Anmeldung fehlgeschlagen</p>
            <p hlmAlertDescription>{{ message }}</p>
          </div>
        }

        <div hlmFieldGroup class="gap-5">
          <hlm-field>
            <label hlmFieldLabel>E-Mail-Adresse</label>
            <input
              hlmInput
              type="email"
              inputmode="email"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
              [formField]="form.email"
            />
            <hlm-field-error validator="required">Bitte gib deine E-Mail-Adresse ein.</hlm-field-error>
            <hlm-field-error validator="email">Bitte gib eine gültige E-Mail-Adresse ein.</hlm-field-error>
          </hlm-field>

          <hlm-field>
            <label hlmFieldLabel>Passwort</label>
            <input hlmInput type="password" autocomplete="current-password" [formField]="form.password" />
            <hlm-field-error validator="required">Bitte gib dein Passwort ein.</hlm-field-error>
          </hlm-field>
        </div>

        <button hlmBtn type="submit" class="w-full" [disabled]="pending()">
          @if (pending()) {
            <hlm-spinner data-icon="inline-start" aria-label="Anmeldung läuft" />
            Wird angemeldet …
          } @else {
            Anmelden
          }
        </button>
      </form>
    </section>
  `,
})
export class LoginForm {
  /** While true the submit button is disabled and shows a spinner. */
  readonly pending = input(false);
  /** User-facing error message, rendered as a destructive alert. */
  readonly error = input<string | null>(null);
  /** Prefill (e.g. dev mock credentials); applied only while the user has not typed yet. */
  readonly initialValue = input<LoginCredentials | undefined>(undefined);
  /** Small muted note under the title (e.g. dev hint). */
  readonly devHint = input<string | null | undefined>(null);

  /** Emits the credentials once the form is valid. */
  readonly submitted = output<LoginCredentials>();

  /** Signal Forms model. */
  private readonly model = signal<LoginCredentials>({ email: '', password: '' });

  protected readonly form = form(this.model, (path) => {
    required(path.email);
    email(path.email);
    required(path.password);
  });

  constructor() {
    effect(() => {
      const value = this.initialValue();
      untracked(() => {
        if (value && !this.form().dirty()) {
          this.model.set({ email: value.email, password: value.password });
        }
      });
    });
  }

  protected onSubmit(event: Event): void {
    event.preventDefault();
    if (this.pending()) {
      return;
    }
    // `submit()` marks all fields as touched and only runs the action when the form is valid.
    void submit(this.form, async () => {
      this.submitted.emit({ ...this.model() });
      return undefined;
    });
  }
}
