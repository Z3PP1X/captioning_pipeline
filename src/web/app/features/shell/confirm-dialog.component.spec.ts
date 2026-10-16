import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { ConfirmDialogComponent } from './confirm-dialog.component';
import { ConfirmService } from '../../core/services/confirm.service';

let fixture: ComponentFixture<ConfirmDialogComponent>;
const flush = async () => {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
  }
};

describe('ConfirmDialogComponent', () => {
  let confirm: ConfirmService;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [ConfirmDialogComponent] });
    confirm = TestBed.inject(ConfirmService);
    fixture = TestBed.createComponent(ConfirmDialogComponent);
  });

  const buttons = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-slot^="alert-dialog-"]'));

  it('renders the pending request in an alert dialog and resolves true on the action button', async () => {
    const answer = confirm.ask({ title: 'Delete Elena?', description: 'Cannot be undone.', confirmLabel: 'Delete person' });
    await flush();

    expect(document.body.textContent).toContain('Delete Elena?');
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull();
    const action = buttons().find((b) => b.dataset['slot'] === 'alert-dialog-action');
    expect(action?.textContent?.trim()).toBe('Delete person');

    action!.click();
    await expect(answer).resolves.toBe(true);
  });

  it('resolves false on cancel', async () => {
    const answer = confirm.ask({ title: 'Reject all?', description: '' });
    await flush();

    buttons().find((b) => b.dataset['slot'] === 'alert-dialog-cancel')!.click();
    await flush();
    await expect(answer).resolves.toBe(false);
  });
});
