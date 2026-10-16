import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ApiService } from '../../core/services/api.service';
import { ApiError } from '../../core/services/api-error';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { UiStateService } from '../../core/services/ui-state.service';
import { CandidateFrame } from '../../core/models/candidate.model';
import { Video } from '../../core/models/video.model';
import { ReselectDialogComponent } from './reselect-dialog.component';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 4; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

const LW = {
  weights: null, default_weights: {}, n_events: 12, accuracy: null, fitted: false, min_events: 50,
  can_fit: false, use_learned_weights: false, active: false,
};
const REPORT = {
  target_count: 2, picks_count: 2, shortfall: 0, promoted: [9], demoted: [1], locked: 0, dup_skipped: 0,
  excluded_rejected: 0, diversity: 0.5, elasticity: 0.2, learned_weights_active: false, chunks: [],
};

describe('ReselectDialogComponent', () => {
  let fixture: ComponentFixture<ReselectDialogComponent>;
  let api: Record<'get' | 'post' | 'put' | 'patch' | 'delete', ReturnType<typeof vi.fn>>;
  let closed: number;

  beforeEach(async () => {
    api = {
      get: vi.fn().mockResolvedValue(LW),
      post: vi.fn().mockResolvedValue({ picks: [], runners: [], reselect: REPORT }),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [{ provide: ApiService, useValue: api }] });
    TestBed.inject(VideoService).currentVideo.set({ id: 'v1', name: 'clip.mp4' } as Video);
    TestBed.inject(CurateService).candidates.set([
      { id: 1, video_id: 'v1', chunk_id: 1, time_s: 1, is_pick: true },
      { id: 2, video_id: 'v1', chunk_id: 1, time_s: 2, is_pick: true },
      { id: 9, video_id: 'v1', chunk_id: 1, time_s: 3, is_pick: false },
    ] as CandidateFrame[]);
    fixture = TestBed.createComponent(ReselectDialogComponent);
    closed = 0;
    fixture.componentInstance.close.subscribe(() => closed++);
    await settle(fixture);
  });

  afterEach(() => fixture.destroy());

  const input = (id: string) => document.getElementById(id) as HTMLInputElement;
  const submit = () =>
    Array.from(document.querySelectorAll<HTMLButtonElement>('button[type="submit"]')).find((b) =>
      b.textContent?.includes('Reselect'),
    )!;

  it('opens prefilled (target = current picks) with the learned-weights status', () => {
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Reselect picks');
    expect(input('reselect-target').value).toBe('2');
    expect(input('reselect-diversity').value).toBe('0.5');
    expect(api.get).toHaveBeenCalledWith('/api/scoring/learned-weights');
    expect(document.body.textContent).toContain('Not fitted');
    expect(TestBed.inject(UiStateService).modalOpen()).toBe(true);
  });

  it('submits the knobs, toasts the summary and closes', async () => {
    const toast = vi.spyOn(TestBed.inject(JobEventService), 'showToast');
    submit().click();
    await settle(fixture);
    expect(api.post).toHaveBeenCalledWith('/api/videos/v1/reselect', {
      target_count: 2,
      diversity: 0.5,
      elasticity: 0.2,
      axis_caps: {},
      cross_dataset_dedup: false,
    });
    expect(toast).toHaveBeenCalledWith('Picks reselected', expect.stringContaining('1 promoted'), 'v1', false, null, false);
    expect(closed).toBe(1);
  });

  it('shows a readable inline error on 409 and stays open', async () => {
    api.post.mockRejectedValue(new ApiError(409, 'Video has an active job', 'Video has an active job'));
    submit().click();
    await settle(fixture);
    expect(document.body.textContent).toContain('queued or running job');
    expect(closed).toBe(0);
  });

  it('blocks submit while a knob is invalid', async () => {
    const el = input('reselect-diversity');
    el.value = '2';
    el.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(document.body.textContent).toContain('Diversity must be between 0 and 1');
    expect(submit().disabled).toBe(true);
  });
});
