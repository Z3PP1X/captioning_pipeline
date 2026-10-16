import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { PROMPT_DIRECTOR_NAVIGATE, PromptRagService } from './prompt-rag.service';
import { HotkeyService } from './hotkey.service';
import { CurateService } from './curate.service';
import { ApiService } from './api.service';
import { ApiError } from './api-error';
import { RagGenerateResponse, RagHistoryEntry } from '../models/prompt-rag.model';

function entry(id: number | null, b: string, extra: Partial<RagHistoryEntry> = {}): RagHistoryEntry {
  const arch = (name: string, prompt: string) => ({ name, prompt, token_count: 5, warnings: [], is_valid: true });
  return {
    id,
    created_at: 1700000000,
    favorite: false,
    batch_id: 'batch',
    batch_index: 0,
    inputs: {
      selected_tags: { equipment: ['hemp_rope'], position: ['seiza'] },
      user_description: 'restored description',
      character: 'sara',
      model: 'local',
      trigger: 'tok',
      top_k: 4,
      n: 1,
      local_url: 'http://ollama:11434',
    },
    result: {
      model: 'local',
      trigger: 'tok',
      character: 'sara',
      grounded: true,
      notices: [],
      archetypes: { a: arch('A', 'tok, a'), b: arch('B', b), c: arch('C', 'tok c') },
      rag_context: [{ id: 7, stem: 'vid_a_0001', score: 0.9, matched_tags: [], img_rel_path: null, full_caption: 'x' }],
    },
    ...extra,
  };
}

describe('PromptRagService', () => {
  let service: PromptRagService;
  let api: Record<'get' | 'post' | 'patch' | 'delete', ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    api = { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() };
    api.get.mockResolvedValue({ items: [], total: 0 });
    TestBed.configureTestingModule({
      providers: [PromptRagService, { provide: ApiService, useValue: api }],
    });
    service = TestBed.inject(PromptRagService);
  });

  it('sends n and exposes every variation as the batch', async () => {
    const v1 = entry(1, 'tok one');
    const v2 = entry(2, 'tok two', { batch_index: 1 });
    const res: RagGenerateResponse = {
      ...v1.result,
      notices: ['1 of 3 variations failed: 429'],
      history_id: 1,
      batch_id: 'batch',
      variations: [v1, v2],
    };
    api.post.mockResolvedValue(res);
    service.batchSize.set(3);

    await service.generatePrompts();

    expect(api.post).toHaveBeenCalledWith('/api/rag/generate', expect.objectContaining({ n: 3 }));
    expect(service.batch()).toHaveLength(2);
    expect(service.generationResult()?.archetypes.b.prompt).toBe('tok one');
    expect(service.generationResult()?.notices).toEqual(['1 of 3 variations failed: 429']);
    expect(api.get).toHaveBeenCalledWith('/api/rag/history?limit=50');

    service.selectVariation(1);
    expect(service.generationResult()?.archetypes.b.prompt).toBe('tok two');
  });

  it('loads favourites only and drops an unstarred entry from that list', async () => {
    const fav = entry(5, 'tok fav', { favorite: true });
    api.get.mockResolvedValue({ items: [fav], total: 1 });
    service.setHistoryFavoritesOnly(true);
    await vi.waitFor(() => expect(service.history()).toHaveLength(1));
    expect(api.get).toHaveBeenLastCalledWith('/api/rag/history?limit=50&favorites=1');

    api.patch.mockResolvedValue({ ...fav, favorite: false });
    await service.toggleFavorite(fav);
    expect(api.patch).toHaveBeenCalledWith('/api/rag/history/5', { favorite: false });
    expect(service.history()).toHaveLength(0);
    expect(service.historyTotal()).toBe(0);
  });

  it('re-lints an inline edit and persists it for saved entries', async () => {
    const e = entry(9, 'tok old');
    service.openEntry(e);
    api.post.mockResolvedValue({
      clean_prompt: 'tok new adult woman',
      warnings: ['Added adult qualifier'],
      token_count: 4,
      is_valid: true,
      archetype: 'short',
      token_counter: 'heuristic',
    });
    const persisted = {
      ...e,
      result: { ...e.result, archetypes: { ...e.result.archetypes, b: { ...e.result.archetypes.b, prompt: 'tok new adult woman', edited: true } } },
    };
    api.patch.mockResolvedValue(persisted);

    const arch = await service.applyEdit(e, 'b', 'new woman');

    expect(api.post).toHaveBeenCalledWith('/api/rag/lint', { prompt: 'new woman', archetype: 'b', trigger: 'tok', character: 'sara' });
    expect(api.patch).toHaveBeenCalledWith('/api/rag/history/9', { prompts: { b: 'tok new adult woman' } });
    expect(arch?.edited).toBe(true);
    expect(service.generationResult()?.archetypes.b.prompt).toBe('tok new adult woman');
  });

  it('keeps unsaved edits local (no PATCH without a history id)', async () => {
    const e = entry(null, 'tok old');
    service.openEntry(e);
    api.post.mockResolvedValue({
      clean_prompt: 'tok edited', warnings: [], token_count: 2, is_valid: true, archetype: 'short', token_counter: 'heuristic',
    });
    await service.applyEdit(e, 'b', 'edited');
    expect(api.patch).not.toHaveBeenCalled();
    expect(service.generationResult()?.archetypes.b).toMatchObject({ prompt: 'tok edited', edited: true });
  });

  it('restores inputs from a history entry', () => {
    service.restoreInputs(entry(3, 'tok'));
    expect(service.selectedTags()).toEqual({ equipment: ['hemp_rope'], surroundings: [], position: ['seiza'], camera_lighting: [] });
    expect(service.userDescription()).toBe('restored description');
    expect(service.selectedCharacter()).toBe('sara');
    expect(service.selectedModel()).toBe('local');
    expect(service.localUrl()).toBe('http://ollama:11434');
  });
});

describe('PromptRagService seeds (Use as prompt seed / More like this frame)', () => {
  let service: PromptRagService;
  let api: Record<'get' | 'post' | 'patch' | 'delete', ReturnType<typeof vi.fn>>;
  let navigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    api = { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() };
    navigate = vi.fn();
    TestBed.configureTestingModule({
      providers: [
        PromptRagService,
        { provide: ApiService, useValue: api },
        { provide: PROMPT_DIRECTOR_NAVIGATE, useValue: navigate },
      ],
    });
    service = TestBed.inject(PromptRagService);
  });

  it('prefills the description from the frame caption (without the trigger) and opens the Prompt Director', async () => {
    api.get.mockResolvedValue({ frame_id: 42, caption: { text: 'tok, a woman kneeling on a mat.', trigger: 'tok', source: 'vlm' } });

    const error = await service.useFrameAsSeed(42, 'clip.mp4 · 00:00:12');

    expect(api.get).toHaveBeenCalledWith('/api/frames/42/caption');
    expect(error).toBeNull();
    expect(service.userDescription()).toBe('a woman kneeling on a mat.');
    expect(service.seedFrame()).toEqual({ frameId: 42, label: 'clip.mp4 · 00:00:12' });
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(service.isSeeding()).toBe(false);
  });

  it('does not navigate when the frame has no caption or is unknown', async () => {
    service.userDescription.set('keep me');
    api.get.mockResolvedValueOnce({ frame_id: 7, caption: null });
    expect(await service.useFrameAsSeed(7)).toContain('no caption yet');

    api.get.mockRejectedValueOnce(new ApiError(404, 'Frame not found', 'Frame not found'));
    expect(await service.useFrameAsSeed(8)).toContain('no longer exists');

    expect(navigate).not.toHaveBeenCalled();
    expect(service.userDescription()).toBe('keep me');
    expect(service.seedFrame()).toBeNull();
  });

  it('More like this: sets seedFrameId, navigates and loads /api/rag/similar', async () => {
    const item = { id: 5, stem: 'clip_c01_1000', score: 0.8, matched_tags: [], img_rel_path: 'x.png', full_caption: 'cap', frame_id: 9 };
    api.get.mockResolvedValue({ results: [item], index_ready: true, embedder: 'clip-vit-b32' });

    service.showSimilarTo(42, 'clip.mp4');
    expect(service.seedFrameId()).toBe(42);
    expect(navigate).toHaveBeenCalledTimes(1);

    await service.loadSimilar();
    expect(api.get).toHaveBeenCalledWith('/api/rag/similar?frame_id=42&top_k=8');
    expect(service.similarResults()).toEqual([item]);
    expect(service.similarIndexReady()).toBe(true);
    expect(service.similarEmbedder()).toBe('clip-vit-b32');
    expect(service.similarLoadedFor()).toBe(42);
  });

  it('treats 409 / index_ready=false as "image index not built" and 404 as an error', async () => {
    service.showSimilarTo(1);
    api.get.mockRejectedValueOnce(new ApiError(409, 'no image index', 'no image index'));
    await service.loadSimilar();
    expect(service.similarIndexReady()).toBe(false);
    expect(service.similarError()).toBeNull();

    service.showSimilarTo(2);
    expect(service.similarIndexReady()).toBeNull();
    api.get.mockResolvedValueOnce({ results: [], index_ready: false, embedder: 'siglip' });
    await service.loadSimilar();
    expect(service.similarIndexReady()).toBe(false);

    service.showSimilarTo(3);
    api.get.mockRejectedValueOnce(new ApiError(404, 'Frame not found', 'Frame not found'));
    await service.loadSimilar();
    expect(service.similarError()).toContain('Frame #3');
    expect(service.similarLoadedFor()).toBe(3);
  });

  it('drops a stale similar response when another frame was requested meanwhile', async () => {
    let resolve!: (v: unknown) => void;
    api.get.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    service.showSimilarTo(1);
    const pending = service.loadSimilar();
    service.showSimilarTo(2);
    resolve({ results: [{ id: 1 }], index_ready: true, embedder: 'x' });
    await pending;
    expect(service.similarResults()).toEqual([]);
    expect(service.similarLoadedFor()).toBeNull();

    service.clearSimilar();
    expect(service.seedFrameId()).toBeNull();
  });

  it('clears one category and the seed on reset', () => {
    service.toggleTag('equipment', 'hemp_rope');
    service.toggleTag('position', 'seiza');
    service.clearCategory('equipment');
    expect(service.selectedTags()['equipment']).toEqual([]);
    expect(service.selectedTags()['position']).toEqual(['seiza']);

    service.seedFrame.set({ frameId: 1 });
    service.clearSelections();
    expect(service.seedFrame()).toBeNull();
  });
});

describe('HotkeyService prompt copy keys', () => {
  it('maps 1/2/3 to copy_prompt_* and ignores modified digits', () => {
    TestBed.configureTestingModule({
      providers: [HotkeyService, CurateService, { provide: ApiService, useValue: { get: vi.fn(), post: vi.fn() } }],
    });
    const hotkeys = TestBed.inject(HotkeyService);
    const calls: string[] = [];
    const offs = (['copy_prompt_1', 'copy_prompt_2', 'copy_prompt_3'] as const).map((a) =>
      hotkeys.register(a, () => calls.push(a)),
    );

    hotkeys.handleKeyDown(new KeyboardEvent('keydown', { key: '1' }));
    hotkeys.handleKeyDown(new KeyboardEvent('keydown', { key: '3' }));
    hotkeys.handleKeyDown(new KeyboardEvent('keydown', { key: '2', metaKey: true }));
    expect(calls).toEqual(['copy_prompt_1', 'copy_prompt_3']);

    offs.forEach((off) => off());
    hotkeys.handleKeyDown(new KeyboardEvent('keydown', { key: '2' }));
    expect(calls).toHaveLength(2);
  });
});
