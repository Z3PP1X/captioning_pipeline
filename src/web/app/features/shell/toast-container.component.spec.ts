import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { toastState } from '@spartan-ng/brain/sonner';
import { ToastContainerComponent } from './toast-container.component';
import { JobEventService, ToastAction } from '../../core/services/job-event.service';
import { ApiService } from '../../core/services/api.service';

let fixture: ComponentFixture<ToastContainerComponent>;
const flush = async () => {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
  }
};
const toastEls = () => Array.from(document.querySelectorAll<HTMLElement>('[data-sonner-toast]'));
const visible = () => toastEls().filter((el) => el.getAttribute('data-removed') !== 'true');

describe('ToastContainerComponent (hlm-toaster)', () => {
  let jobEvents: JobEventService;

  beforeEach(() => {
    toastState.reset();
    TestBed.configureTestingModule({
      imports: [ToastContainerComponent],
      providers: [{ provide: ApiService, useValue: {} }],
    });
    jobEvents = TestBed.inject(JobEventService);
    fixture = TestBed.createComponent(ToastContainerComponent);
  });

  afterEach(() => {
    vi.useRealTimers();
    fixture.destroy();
    toastState.reset();
  });

  it('renders service toasts through the Spartan sonner with title and description', async () => {
    await flush();
    jobEvents.showToast('Settings not saved', 'max_edge must be at least 256', null, true);
    jobEvents.showToast('API key saved', 'The OpenRouter key is stored on the server.');
    await flush();

    const els = visible();
    expect(els.length).toBe(2);
    const error = els.find((el) => el.textContent?.includes('Settings not saved'))!;
    expect(error.getAttribute('data-type')).toBe('error');
    expect(error.textContent).toContain('max_edge must be at least 256');
    // Errors are announced assertively
    expect(error.getAttribute('aria-live')).toBe('assertive');
    const info = els.find((el) => el.textContent?.includes('API key saved'))!;
    expect(info.getAttribute('data-type')).toBe('info');
    expect(info.getAttribute('aria-live')).toBe('polite');
  });

  it('emits the typed action and removes the toast from the service', async () => {
    const requests: ToastAction[] = [];
    fixture.componentInstance.actionRequest.subscribe((a) => requests.push(a));
    await flush();

    jobEvents.showToast('Extraction complete', 'clip.mp4 is ready.', 'v1', false, {
      label: 'Start curating',
      tab: 'curate',
      videoId: 'v1',
    });
    await flush();

    const el = visible()[0];
    expect(el.getAttribute('data-type')).toBe('success');
    const button = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Start curating');
    expect(button).toBeTruthy();
    button!.click();
    await flush();

    expect(requests).toEqual([{ label: 'Start curating', tab: 'curate', videoId: 'v1' }]);
    expect(jobEvents.toasts()).toEqual([]);
  });

  it('keeps error toasts until closed, auto-dismisses the others via the service timer', async () => {
    vi.useFakeTimers();
    await flush();
    jobEvents.showToast('Job error', 'ffmpeg exited with 1', null, true);
    jobEvents.showToast('Auto-tagging started', 'Suggestions appear when the job finishes.');
    await flush();
    expect(visible().length).toBe(2);

    vi.advanceTimersByTime(10_000);
    await flush();

    expect(jobEvents.toasts().map((t) => t.title)).toEqual(['Job error']);
    expect(visible().map((el) => el.getAttribute('data-type'))).toEqual(['error']);

    // The sonner close button dismisses it in the service as well
    const close = visible()[0].querySelector<HTMLButtonElement>('[data-close-button]');
    expect(close?.getAttribute('aria-label')).toBe('Close toast');
    close!.click();
    await flush();
    expect(jobEvents.toasts()).toEqual([]);
  });

  it('dismissToast() on the service removes the sonner toast', async () => {
    await flush();
    jobEvents.showToast('Video exported', '12 frames exported.', 'v1', false, null, true);
    await flush();
    const id = jobEvents.toasts()[0].id;
    expect(toastState.toasts().map((t) => t.id)).toEqual([id]);

    jobEvents.dismissToast(id);
    await flush();
    expect(toastState.toasts()).toEqual([]);
  });
});
