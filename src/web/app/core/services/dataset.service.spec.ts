import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { DatasetService } from './dataset.service';
import { ApiService } from './api.service';

describe('DatasetService.loadExportedFrames', () => {
  let service: DatasetService;
  let api: Record<'get' | 'post', ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    api = { get: vi.fn(), post: vi.fn() };
    TestBed.configureTestingModule({ providers: [DatasetService, { provide: ApiService, useValue: api }] });
    service = TestBed.inject(DatasetService);
  });

  it('keeps only exported frames (is_captioned) of the video, sorted by time', async () => {
    api.get.mockResolvedValue({
      picks: [
        { id: 2, t: 9, is_captioned: true },
        { id: 3, t: 1, is_captioned: false },
      ],
      runners: [{ id: 4, t: 4, is_captioned: true, preview_url: '/p/4.webp' }],
    });

    await service.loadExportedFrames('vid a');

    expect(api.get).toHaveBeenCalledWith('/api/videos/vid%20a/curation-300');
    const st = service.exportedFrames()['vid a'];
    expect(st.loading).toBe(false);
    expect(st.frames.map((f) => [f.id, f.time_s, f.preview_url])).toEqual([
      [4, 4, '/p/4.webp'],
      [2, 9, '/api/frames/2/preview'],
    ]);
  });

  it('records the error', async () => {
    api.get.mockRejectedValue(new Error('offline'));
    await service.loadExportedFrames(7);
    expect(service.exportedFrames()['7']).toEqual({ loading: false, frames: [], error: 'offline' });
  });
});
