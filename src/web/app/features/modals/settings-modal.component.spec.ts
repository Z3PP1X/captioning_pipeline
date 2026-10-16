import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { SettingsModalComponent } from './settings-modal.component';
import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import { UiStateService } from '../../core/services/ui-state.service';
import { ConfirmService } from '../../core/services/confirm.service';
import { ConfirmDialogComponent } from '../shell/confirm-dialog.component';

let fixture: ComponentFixture<SettingsModalComponent>;
const flush = async () => {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
  }
};

const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const saveButton = () =>
  Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Save settings'))!;

function type(el: HTMLInputElement, value: string): void {
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('SettingsModalComponent (hlm-dialog + Signal Forms)', () => {
  let settings: SettingsService;
  let closed: number;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [SettingsModalComponent],
      providers: [{ provide: ApiService, useValue: { get: vi.fn(), put: vi.fn() } }],
    });
    settings = TestBed.inject(SettingsService);
    settings.settings.set({
      default_k: 7,
      default_rule: 'both',
      sample_fps: 2,
      survivor_frac: 0.25,
      trigger_word: 'restr_elg',
      max_edge: 1024,
      snap_to_cuts: false,
      snap_window_s: 2,
    });
    fixture = TestBed.createComponent(SettingsModalComponent);
    closed = 0;
    fixture.componentInstance.close.subscribe(() => closed++);
  });

  afterEach(() => fixture.destroy());

  const open = async () => {
    fixture.componentRef.setInput('isOpen', true);
    await flush();
  };

  it('renders nothing while closed and an accessible dialog with the stored values when opened', async () => {
    await flush();
    expect(dialog()).toBeNull();

    await open();
    const d = dialog()!;
    expect(d.getAttribute('aria-modal')).toBe('true');
    const title = document.getElementById(d.getAttribute('aria-labelledby')!);
    expect(title?.textContent?.trim()).toBe('Pipeline settings');
    expect(input('setting-default-k').value).toBe('7');
    expect(input('setting-max-edge').value).toBe('1024');
    expect(input('setting-trigger-word').value).toBe('restr_elg');
    expect((document.getElementById('setting-default-rule') as HTMLSelectElement).value).toBe('both');
    expect(TestBed.inject(UiStateService).isModalOpen('settings')).toBe(true);
  });

  it('shows the rule message for out-of-range / empty / fractional values and disables Save', async () => {
    await open();

    type(input('setting-max-edge'), '100');
    await flush();
    expect(document.body.textContent).toContain('Max export dimension must be between 256 and 8192.');
    expect(input('setting-max-edge').getAttribute('aria-invalid')).toBe('true');
    expect(saveButton().disabled).toBe(true);
    expect(document.body.textContent).toContain('Fix 1 field to save.');

    type(input('setting-sample-fps'), '');
    await flush();
    expect(document.body.textContent).toContain('Sampling FPS must be a number.');
    expect(document.body.textContent).toContain('Fix 2 fields to save.');

    type(input('setting-max-edge'), '2048.5');
    await flush();
    expect(document.body.textContent).toContain('Max export dimension must be a whole number.');

    // Saving is impossible while invalid, even when called directly
    const save = vi.spyOn(settings, 'saveSettings');
    await fixture.componentInstance.onSave();
    expect(save).not.toHaveBeenCalled();
  });

  it('saves the parsed form values and emits close', async () => {
    await open();
    const save = vi.spyOn(settings, 'saveSettings').mockResolvedValue();

    type(input('setting-default-k'), '12');
    type(input('setting-trigger-word'), 'new_word');
    await flush();
    expect(saveButton().disabled).toBe(false);

    saveButton().click();
    await flush();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      default_k: 12,
      default_rule: 'both',
      sample_fps: 2,
      survivor_frac: 0.25,
      trigger_word: 'new_word',
      max_edge: 1024,
      snap_to_cuts: false,
      snap_window_s: 2,
      tag_suggest_min_score: 0.5,
      cut_detector: 'content',
    }));
    expect(closed).toBe(1);
  });

  it('resets edits from the stored settings on every open', async () => {
    await open();
    type(input('setting-default-k'), '99');
    await flush();

    fixture.componentRef.setInput('isOpen', false);
    await flush();
    await open();
    expect(input('setting-default-k').value).toBe('7');
    expect(fixture.componentInstance.isValid()).toBe(true);
  });

  it('emits close when dismissed through the dialog (built-in close button)', async () => {
    await open();
    const closeBtn = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-slot="dialog-close"]')).find((b) =>
      b.textContent?.includes('Close settings'),
    )!;
    closeBtn.click();
    await flush();
    await new Promise((r) => setTimeout(r, 0));
    await flush();
    expect(closed).toBeGreaterThanOrEqual(1);
  });

  it('Escape inside the dialog closes it (CDK overlay), alongside the HotkeyService escape action', async () => {
    await open();
    const target = (document.activeElement as HTMLElement) ?? document.body;
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await flush();
    await new Promise((r) => setTimeout(r, 0));
    await flush();
    expect(closed).toBeGreaterThanOrEqual(1);
  });

  it('a confirm alert-dialog opened on top stacks above the settings dialog', async () => {
    const confirmFixture = TestBed.createComponent(ConfirmDialogComponent);
    await open();
    TestBed.inject(ConfirmService).ask({ title: 'Discard?', description: '' });
    for (let i = 0; i < 3; i++) {
      confirmFixture.detectChanges();
      await confirmFixture.whenStable();
    }
    await flush();

    const panes = Array.from(document.querySelectorAll('.cdk-overlay-container [role="dialog"], .cdk-overlay-container [role="alertdialog"]'));
    expect(panes.map((p) => p.getAttribute('role'))).toEqual(['dialog', 'alertdialog']);
    TestBed.inject(ConfirmService).settle(false);
    confirmFixture.destroy();
  });
});
