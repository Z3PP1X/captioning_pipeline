import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { ConfirmService } from './confirm.service';
import { UiStateService } from './ui-state.service';

describe('ConfirmService', () => {
  let confirm: ConfirmService;
  let ui: UiStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    confirm = TestBed.inject(ConfirmService);
    ui = TestBed.inject(UiStateService);
  });

  it('resolves true on confirm and marks the confirm modal open while pending', async () => {
    const answer = confirm.ask({ title: 'Delete?', description: 'Really?' });
    expect(confirm.pending()?.title).toBe('Delete?');
    expect(confirm.pending()?.destructive).toBe(true);
    expect(ui.isModalOpen('confirm')).toBe(true);
    expect(ui.modalOpen()).toBe(true);

    confirm.settle(true);
    await expect(answer).resolves.toBe(true);
    expect(confirm.pending()).toBeNull();
    expect(ui.modalOpen()).toBe(false);
  });

  it('resolves false on cancel and ignores a second settle', async () => {
    const answer = confirm.ask({ title: 'Keep all?', description: '', destructive: false });
    confirm.settle(false);
    confirm.settle(true);
    await expect(answer).resolves.toBe(false);
  });

  it('cancels a pending request when a new one is asked', async () => {
    const first = confirm.ask({ title: 'First', description: '' });
    const second = confirm.ask({ title: 'Second', description: '' });
    await expect(first).resolves.toBe(false);
    expect(confirm.pending()?.title).toBe('Second');
    confirm.settle(true);
    await expect(second).resolves.toBe(true);
  });
});
