import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { form, FormField, required, submit } from '@angular/forms/signals';
import { HlmFieldImports } from '@spartan-ng/helm/field';
import { HlmTextarea } from '@spartan-ng/helm/textarea';
import { HlmInput } from './hlm-input';

@Component({
  imports: [ReactiveFormsModule, HlmFieldImports, HlmInput, HlmTextarea],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form">
      <hlm-field>
        <label hlmFieldLabel>E-Mail</label>
        <input hlmInput data-testid="by-name" formControlName="email" />
      </hlm-field>
      <hlm-field>
        <label hlmFieldLabel>Nachricht</label>
        <textarea hlmTextarea data-testid="textarea-by-name" formControlName="message"></textarea>
      </hlm-field>
      <input hlmInput data-testid="by-control" [formControl]="standalone" />
    </form>
  `,
})
class FormHost {
  readonly form = new FormGroup({
    email: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    message: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });
  readonly standalone = new FormControl('', { nonNullable: true, validators: [Validators.required] });
}

@Component({
  imports: [FormField, HlmFieldImports, HlmInput],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-field>
      <label hlmFieldLabel>E-Mail</label>
      <input hlmInput data-testid="signal" [formField]="form.email" />
    </hlm-field>
  `,
})
class SignalFormHost {
  readonly form = form(signal({ email: '' }), (path) => required(path.email));
}

describe('HlmInput with Signal Forms ([formField])', () => {
  it('only sets aria-invalid after submit/touch', async () => {
    const fixture = TestBed.createComponent(SignalFormHost);
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="signal"]')!;
    expect(input.getAttribute('aria-invalid')).toBeNull();

    await submit(fixture.componentInstance.form, async () => undefined);
    await fixture.whenStable();
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });
});

describe('HlmInput / HlmTextarea aria-invalid', () => {
  let fixture: ComponentFixture<FormHost>;

  const el = (testId: string) =>
    (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${testId}"]`) as HTMLElement;

  /** Lets BrnFieldControl resolve formControlName (microtask) and re-render. */
  async function settle(): Promise<void> {
    await fixture.whenStable();
    await Promise.resolve();
    await fixture.whenStable();
  }

  beforeEach(async () => {
    fixture = TestBed.createComponent(FormHost);
    await settle();
  });

  it.each(['by-name', 'textarea-by-name', 'by-control'])(
    'does not mark a pristine, empty required control (%s) as invalid',
    (testId) => {
      const control = el(testId);
      expect(control.getAttribute('data-invalid')).toBe('true'); // raw validity is still exposed
      expect(control.getAttribute('aria-invalid')).toBeNull();
      expect(control.getAttribute('data-matches-spartan-invalid')).toBeNull();
    },
  );

  it('sets aria-invalid once touched and removes it when valid (formControlName)', async () => {
    fixture.componentInstance.form.markAllAsTouched();
    fixture.componentInstance.standalone.markAsTouched();
    await settle();

    for (const testId of ['by-name', 'textarea-by-name', 'by-control']) {
      expect(el(testId).getAttribute('aria-invalid')).toBe('true');
    }

    fixture.componentInstance.form.controls.email.setValue('anna@example.com');
    await settle();
    expect(el('by-name').getAttribute('aria-invalid')).toBeNull();
    expect(el('textarea-by-name').getAttribute('aria-invalid')).toBe('true');
  });

  it('still wires the field label to the control id', () => {
    const label = (fixture.nativeElement as HTMLElement).querySelector('label') as HTMLLabelElement;
    expect(label.htmlFor).toBe(el('by-name').id);
    expect(el('by-name').id).toMatch(/^hlm-input-\d+$/);
  });
});
