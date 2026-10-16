import { describe, it, expect } from 'vitest';
import { RagHistoryEntry } from '../models/prompt-rag.model';
import { buildPromptJsonl, buildPromptTxt, exportFilename } from './prompt-export.utils';

function entry(id: number, b: string, extra: Partial<RagHistoryEntry> = {}): RagHistoryEntry {
  const arch = (name: string, prompt: string) => ({ name, prompt, token_count: 10, warnings: [], is_valid: true });
  return {
    id,
    created_at: 1700000000 + id,
    favorite: false,
    batch_id: 'batch1',
    batch_index: id,
    inputs: {
      selected_tags: { equipment: ['hemp_rope'] },
      user_description: 'desc',
      character: null,
      model: 'm',
      trigger: 'tok',
      top_k: 4,
      n: 2,
      local_url: null,
    },
    result: {
      model: 'm',
      trigger: 'tok',
      character: null,
      grounded: true,
      notices: [],
      archetypes: { a: arch('A', `tok, tags ${id}`), b: arch('B', b), c: arch('C', '') },
      rag_context: [
        { id: 1, stem: 'vid_a_0001', score: 0.9, matched_tags: [], img_rel_path: null, full_caption: 'x' },
      ],
    },
    ...extra,
  };
}

describe('prompt-export.utils', () => {
  it('builds one prompt per line, collapsing newlines inside prompts', () => {
    const txt = buildPromptTxt([entry(1, 'tok first\nline'), entry(2, 'tok second')]);
    expect(txt).toBe('tok first line\ntok second\n');
  });

  it('exports several archetypes in the requested order and skips empty prompts', () => {
    const txt = buildPromptTxt([entry(1, 'tok b1')], ['b', 'a', 'c']);
    expect(txt).toBe('tok b1\ntok, tags 1\n');
    expect(buildPromptTxt([])).toBe('');
  });

  it('builds JSONL with inputs and exemplar stems per prompt', () => {
    const lines = buildPromptJsonl([entry(3, 'tok b3')], ['b', 'c']).trim().split('\n');
    expect(lines).toHaveLength(1);
    const row = JSON.parse(lines[0]);
    expect(row).toMatchObject({
      prompt: 'tok b3',
      archetype: 'b',
      history_id: 3,
      trigger: 'tok',
      selected_tags: { equipment: ['hemp_rope'] },
      user_description: 'desc',
      exemplar_stems: ['vid_a_0001'],
      edited: false,
    });
  });

  it('formats a sortable filename', () => {
    expect(exportFilename('history', 'jsonl', new Date(2026, 0, 5, 7, 9))).toBe('krea-prompts-history-20260105-0709.jsonl');
  });
});
