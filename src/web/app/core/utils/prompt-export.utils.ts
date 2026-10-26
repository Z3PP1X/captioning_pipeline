import { ArchetypeKey, RagHistoryEntry } from '../models/prompt-rag.model';

/** Display / export order: B is the recommended Krea format. */
export const ARCHETYPE_EXPORT_ORDER: readonly ArchetypeKey[] = ['b', 'a', 'c'];

/** Prompt text on a single line (Krea batch lists are newline separated). */
function oneLine(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

function promptsOf(entry: RagHistoryEntry, keys: readonly ArchetypeKey[]): { key: ArchetypeKey; prompt: string }[] {
  return keys
    .map((key) => ({ key, prompt: oneLine(entry.result?.archetypes?.[key]?.prompt ?? '') }))
    .filter((p) => p.prompt.length > 0);
}

/** Newline-separated prompts (one per line) for the given archetypes, entry by entry. */
export function buildPromptTxt(entries: readonly RagHistoryEntry[], keys: readonly ArchetypeKey[] = ['b']): string {
  const lines = entries.flatMap((e) => promptsOf(e, keys).map((p) => p.prompt));
  return lines.length ? lines.join('\n') + '\n' : '';
}

/** The JSONL record of one archetype prompt of a history entry. */
function promptRecord(entry: RagHistoryEntry, key: ArchetypeKey, prompt: string, stems: string[]): string {
  const arch = entry.result.archetypes[key];
  return JSON.stringify({
    prompt,
    archetype: key,
    archetype_name: arch.name,
    token_count: arch.token_count,
    edited: arch.edited ?? false,
    history_id: entry.id,
    batch_id: entry.batch_id,
    created_at: entry.created_at,
    favorite: entry.favorite,
    model: entry.result.model,
    trigger: entry.result.trigger,
    character: entry.result.character ?? null,
    selected_tags: entry.inputs?.selected_tags ?? {},
    user_description: entry.inputs?.user_description ?? '',
    exemplar_stems: stems,
  });
}

/** One JSON object per prompt: prompt, archetype, inputs and exemplar stems (for Krea batches / LoRA val sets). */
export function buildPromptJsonl(
  entries: readonly RagHistoryEntry[],
  keys: readonly ArchetypeKey[] = ARCHETYPE_EXPORT_ORDER,
): string {
  const lines: string[] = [];
  for (const entry of entries) {
    const stems = (entry.result?.rag_context ?? []).map((c) => c.stem);
    for (const { key, prompt } of promptsOf(entry, keys)) {
      lines.push(promptRecord(entry, key, prompt, stems));
    }
  }
  return lines.length ? lines.join('\n') + '\n' : '';
}

/** `krea-prompts-<suffix>-YYYYMMDD-HHMM.<ext>` in local time. */
export function exportFilename(suffix: string, ext: 'txt' | 'jsonl', now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `krea-prompts-${suffix}-${stamp}.${ext}`;
}

/** Trigger a client-side download (no server round trip). */
export function downloadText(filename: string, content: string, mime = 'text/plain'): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
