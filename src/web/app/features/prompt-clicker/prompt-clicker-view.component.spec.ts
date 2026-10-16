import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ApiService } from '../../core/services/api.service';
import { ApiError } from '../../core/services/api-error';
import { PROMPT_DIRECTOR_NAVIGATE, PromptRagService } from '../../core/services/prompt-rag.service';
import { PromptClickerViewComponent } from './prompt-clicker-view.component';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 4; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

const STATUS = {
  ready: false,
  indexed: 0,
  index: null,
  sources: { source: 'captions_table', path: null, caption_files: 12, searched: [] },
  build: { state: 'idle', progress: 0, step: '', error: null, started_at: null, finished_at: null },
  build_command: 'uv run python scripts/index_rag.py',
};
const CATEGORIES = {
  equipment: { title: 'Equipment', icon: '', description: 'Props', groups: { Rope: [{ id: 'hemp_rope', label: 'Hemp rope', keywords: [] }] } },
};

describe('PromptClickerViewComponent (K4 layout)', () => {
  let fixture: ComponentFixture<PromptClickerViewComponent>;
  let api: Record<'get' | 'post' | 'patch' | 'delete', ReturnType<typeof vi.fn>>;
  let el: HTMLElement;

  function routeGet(similar: () => Promise<unknown>) {
    api.get.mockImplementation((path: string) => {
      if (path === '/api/rag/categories') return Promise.resolve({ categories: CATEGORIES });
      if (path === '/api/rag/presets') return Promise.resolve({ presets: [] });
      if (path === '/api/rag/characters') return Promise.resolve({ characters: {} });
      if (path === '/api/rag/status') return Promise.resolve(STATUS);
      if (path.startsWith('/api/rag/history')) return Promise.resolve({ items: [], total: 0 });
      if (path.startsWith('/api/rag/similar')) return similar();
      return Promise.reject(new Error('unexpected ' + path));
    });
  }

  beforeEach(() => {
    api = { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() };
    TestBed.configureTestingModule({
      providers: [{ provide: ApiService, useValue: api }, { provide: PROMPT_DIRECTOR_NAVIGATE, useValue: vi.fn() }],
    });
  });

  afterEach(() => fixture?.destroy());

  async function mount(): Promise<void> {
    fixture = TestBed.createComponent(PromptClickerViewComponent);
    el = fixture.nativeElement;
    await settle(fixture);
  }

  it('renders the signature H1, ink toggle chips, the sticky Generate bar and the index-not-built state', async () => {
    routeGet(() => Promise.resolve({ results: [], index_ready: true, embedder: 'x' }));
    await mount();

    expect(el.querySelector('h1')?.textContent).toContain('Prompt Director');
    expect(el.textContent).toContain('RAG index not built yet');
    expect(el.textContent).toContain('Not grounded');

    const chip = Array.from(el.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')).find((b) =>
      b.textContent?.includes('Hemp rope'),
    )!;
    chip.click();
    await settle(fixture);
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(chip.className).toContain('bg-secondary');
    expect(chip.className).not.toContain('bg-primary');

    const bar = el.querySelector('[aria-label="Generate"]')!;
    expect(bar.className).toContain('sticky');
    expect(bar.textContent).toContain('Generate prompts');
    expect(bar.textContent).toContain('1 tags selected');
  });

  it('loads "more like this frame" for the seed frame and renders the exemplars', async () => {
    routeGet(() =>
      Promise.resolve({
        results: [{ id: 3, stem: 'clip_c01_1200', score: 0.91, matched_tags: [], img_rel_path: null, full_caption: 'tok, rope on a wooden floor', frame_id: 77 }],
        index_ready: true,
        embedder: 'siglip-base',
      }),
    );
    TestBed.inject(PromptRagService).showSimilarTo(42, 'clip.mp4');
    await mount();

    expect(api.get).toHaveBeenCalledWith('/api/rag/similar?frame_id=42&top_k=8');
    expect(el.textContent).toContain('More like frame #42');
    expect(el.textContent).toContain('clip_c01_1200');
    expect(el.textContent).toContain('siglip-base');

    // "Use as description" puts the exemplar caption (minus the trigger) into the description
    TestBed.inject(PromptRagService).selectedTrigger.set('tok');
    Array.from(el.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Use as description'))!.click();
    await settle(fixture);
    expect(TestBed.inject(PromptRagService).userDescription()).toBe('rope on a wooden floor');
    expect(el.textContent).toContain('Seeded from frame #77');
  });

  it('shows the not-ready state when the image index is missing (409)', async () => {
    routeGet(() => Promise.reject(new ApiError(409, 'no image index', 'no image index')));
    TestBed.inject(PromptRagService).showSimilarTo(5);
    await mount();
    expect(el.textContent).toContain('Image index not built yet');
  });
});
