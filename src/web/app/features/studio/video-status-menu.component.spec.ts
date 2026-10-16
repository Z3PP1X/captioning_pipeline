import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { VideoStatusMenuComponent } from './video-status-menu.component';
import { Video } from '../../core/models/video.model';
import { ActiveJob } from '../../core/models/job.model';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { ConfirmService } from '../../core/services/confirm.service';
import { JobEventService } from '../../core/services/job-event.service';

const baseVideo: Video = {
  id: 'v1',
  name: 'clip.mp4',
  path: '/videos/clip.mp4',
  slug: 'clip',
  width: 1920,
  height: 1080,
  duration: 60,
  fps: 25,
  frame_count: 1500,
  broken: false,
  status: 'curating_300',
  chunk_count: 3,
  kept_count: 120,
  export_count: 0,
};

let fixture: ComponentFixture<VideoStatusMenuComponent>;
const flush = async () => {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
  }
};
const menuItems = () =>
  Array.from(document.querySelectorAll<HTMLButtonElement>('[data-slot="dropdown-menu-item"]')).map((b) => ({
    el: b,
    text: b.textContent?.replace(/\s+/g, ' ').trim() ?? '',
  }));

describe('VideoStatusMenuComponent (decision 3)', () => {
  let updateStatus: ReturnType<typeof vi.fn>;
  let ask: ReturnType<typeof vi.fn>;
  let showToast: ReturnType<typeof vi.fn>;
  const activeJobs = signal(new Map<number, ActiveJob>());

  const create = async (video: Partial<Video>) => {
    fixture = TestBed.createComponent(VideoStatusMenuComponent);
    fixture.componentRef.setInput('video', { ...baseVideo, ...video });
    await flush();
  };
  const openMenu = async () => {
    fixture.nativeElement.querySelector('[data-testid="video-status-menu-trigger"]').click();
    await flush();
  };

  beforeEach(() => {
    updateStatus = vi.fn().mockResolvedValue(undefined);
    ask = vi.fn().mockResolvedValue(true);
    showToast = vi.fn();
    activeJobs.set(new Map());
    TestBed.configureTestingModule({
      imports: [VideoStatusMenuComponent],
      providers: [
        { provide: VideoService, useValue: { updateStatus } },
        { provide: ChunkService, useValue: { chunks: signal([]) } },
        { provide: ConfirmService, useValue: { ask } },
        { provide: JobEventService, useValue: { activeJobs, showToast } },
      ],
    });
  });

  afterEach(() => fixture?.destroy());

  it('shows the status as a badge (icon + text) instead of a select', async () => {
    await create({ status: 'curating_300' });
    const badge = fixture.nativeElement.querySelector('[data-testid="video-status-badge"]') as HTMLElement;
    expect(badge.getAttribute('data-variant')).toBe('info');
    expect(badge.textContent).toContain('Curating');
    expect(badge.querySelector('ng-icon')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('select')).toBeNull();
  });

  it('Skip sets the status to skipped without a confirmation', async () => {
    await create({ status: 'curating_300' });
    await openMenu();
    const items = menuItems();
    // Status equals the derived one: no Restore entry
    expect(items.some((i) => i.text.startsWith('Restore'))).toBe(false);
    items.find((i) => i.text === 'Skip video')!.el.click();
    await flush();
    expect(ask).not.toHaveBeenCalled();
    expect(updateStatus).toHaveBeenCalledWith('v1', 'skipped');
  });

  it('Restore moves a skipped video back to the derived status', async () => {
    await create({ status: 'skipped', kept_count: 0, chunk_count: 2 });
    await openMenu();
    const items = menuItems();
    expect(items.some((i) => i.text === 'Skip video')).toBe(false);
    const restore = items.find((i) => i.text.startsWith('Restore'))!;
    expect(restore.text).toBe('Restore to Chunked');
    restore.el.click();
    await flush();
    expect(updateStatus).toHaveBeenCalledWith('v1', 'chunked');
  });

  it('hides Restore while a job owns the status', async () => {
    activeJobs.set(new Map([[7, { job_id: 7, video_id: 'v1', video_name: 'clip.mp4', progress: 0.4, step: 'Scoring', type: 'extract_750' }]]));
    await create({ status: 'extracting_750' });
    expect(fixture.nativeElement.textContent).toContain('Extracting · 40%');
    await openMenu();
    expect(menuItems().some((i) => i.text.startsWith('Restore'))).toBe(false);
  });

  it('advanced: non-derived jumps ask for confirmation, derived ones do not', async () => {
    await create({ status: 'skipped' });
    const cmp = fixture.componentInstance;
    expect(cmp.derived()).toBe('curating_300');

    ask.mockResolvedValueOnce(false);
    await cmp.setStatus('exported');
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0][0].title).toBe('Set status to "Exported"?');
    expect(ask.mock.calls[0][0].description).toContain('suggests "Curating (300)"');
    expect(updateStatus).not.toHaveBeenCalled();

    await cmp.setStatus('exported');
    expect(updateStatus).toHaveBeenCalledWith('v1', 'exported');

    ask.mockClear();
    await cmp.setStatus('curating_300');
    expect(ask).not.toHaveBeenCalled();
    expect(updateStatus).toHaveBeenLastCalledWith('v1', 'curating_300');
  });

  it('lists every status in the advanced submenu, the current one disabled', async () => {
    await create({ status: 'chunked', kept_count: 0 });
    await openMenu();
    const advanced = document.querySelector<HTMLButtonElement>('[data-slot="dropdown-menu-sub-trigger"]')!;
    expect(advanced.textContent).toContain('Advanced: set status…');
    advanced.click();
    await flush();
    const sub = Array.from(document.querySelectorAll('[data-slot="dropdown-menu-sub"] [data-slot="dropdown-menu-item"]'));
    expect(sub.length).toBe(11);
    const current = sub.find((el) => el.getAttribute('aria-current') === 'true')!;
    expect(current.textContent).toContain('Chunked');
    expect(current.hasAttribute('data-disabled')).toBe(true);
  });

  it('reports a failed PATCH as a persistent error toast', async () => {
    updateStatus.mockRejectedValueOnce(new Error('HTTP 500'));
    await create({ status: 'curating_300' });
    await fixture.componentInstance.skip();
    expect(showToast).toHaveBeenCalledWith('Status not changed', 'HTTP 500', 'v1', true);
    expect(fixture.componentInstance.busy()).toBe(false);
  });
});
