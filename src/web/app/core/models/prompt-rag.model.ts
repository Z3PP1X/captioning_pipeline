export interface TaxonomyOption {
  id: string;
  label: string;
  keywords: string[];
}

export interface TaxonomyCategory {
  title: string;
  icon: string;
  description: string;
  groups: Record<string, TaxonomyOption[]>;
}

export interface CuratedPreset {
  id: string;
  title: string;
  description: string;
  tags: Record<string, string[]>;
  suggested_description: string;
}

/** Exemplar as returned in `rag_context` (generate); `/retrieve` adds split + domain texts. */
export interface RagItemPreview {
  id: number;
  stem: string;
  score: number;
  matched_tags: string[];
  img_rel_path: string | null;
  full_caption: string;
  split?: string;
  /** Set for captions-table exemplars (null for file sources). */
  frame_id?: number | null;
  /** Video id (captions table) or stem prefix (files). */
  source_id?: string | null;
  equipment_text?: string;
  surroundings_text?: string;
  position_text?: string;
  lighting_text?: string;
}

export interface RagRetrieveResponse {
  results: RagItemPreview[];
  index_ready: boolean;
}

/** `GET /api/rag/similar?frame_id=&top_k=` ("more like this frame"; results shaped like `/retrieve`). */
export interface RagSimilarResponse {
  results: RagItemPreview[];
  /** false when no image index exists yet (results empty). */
  index_ready: boolean;
  embedder: string;
}

/** Frame the Prompt Director was seeded from (Curate lightbox / Dataset). */
export interface PromptSeedFrame {
  frameId: number;
  /** Short context for the UI, e.g. "clip.mp4 · 00:01:12". */
  label?: string | null;
}

export type RagBuildState = 'idle' | 'running' | 'done' | 'error';

/** `GET /api/rag/status` and `POST /api/rag/index`. */
export interface RagIndexStatus {
  ready: boolean;
  indexed: number;
  index: {
    count: number;
    source: 'captions_table' | 'curated' | 'finished_merged' | null;
    source_path: string | null;
    built_at: number;
    embedding_model: string | null;
  } | null;
  /** null while a build is running (folder scan skipped). */
  sources: {
    source: 'captions_table' | 'curated' | 'finished_merged' | null;
    path: string | null;
    caption_files: number;
    searched: string[];
  } | null;
  build: {
    state: RagBuildState;
    progress: number;
    step: string;
    error: string | null;
    started_at: number | null;
    finished_at: number | null;
  };
  build_command: string;
  openrouter_key_configured?: boolean;
  /** Retrieval pipeline info: RRF fusion, BM25 (SQLite FTS5) availability, token counter in use. */
  retrieval?: {
    fusion: 'rrf';
    bm25: boolean;
    token_counter: 'qwen' | 'heuristic';
  };
}

export interface CharacterProfile {
  id: string;
  name: string;
  age: number;
  avatar: string;
  summary: string;
  description: string;
  tag_tokens: string[];
}

export interface PromptArchetype {
  name: string;
  prompt: string;
  token_count: number;
  warnings: string[];
  is_valid: boolean;
  /** Set once the prompt was edited inline and re-linted. */
  edited?: boolean;
}

export type ArchetypeKey = 'a' | 'b' | 'c';

export interface RagGenerateRequest {
  selected_tags: Record<string, string[]>;
  user_description: string;
  character?: string | null;
  model: string;
  trigger: string;
  top_k?: number;
  local_url?: string;
  api_key?: string;
  /** Batch "Generate N" (1-10). */
  n?: number;
}

/** One generated prompt set (a history entry's `result`). */
export interface RagGenerateResult {
  model: string;
  trigger: string;
  character?: string | null;
  /** false when no exemplars were retrieved (index missing/empty). */
  grounded: boolean;
  notices?: string[];
  archetypes: {
    a: PromptArchetype;
    b: PromptArchetype;
    c: PromptArchetype;
  };
  rag_context: RagItemPreview[];
}

/** Inputs stored with every history entry (no API key). */
export interface RagGenerateInputs {
  selected_tags: Record<string, string[]>;
  user_description: string;
  character: string | null;
  model: string;
  trigger: string;
  top_k: number;
  n: number;
  local_url: string | null;
}

/** Row of `rag_history` (state DB). `id` is null only if the history write failed. */
export interface RagHistoryEntry {
  id: number | null;
  created_at: number;
  favorite: boolean;
  batch_id: string | null;
  batch_index: number;
  inputs: RagGenerateInputs;
  result: RagGenerateResult;
}

/** `POST /api/rag/generate`: the first variation's fields at top level (legacy shape) plus all variations. */
export interface RagGenerateResponse extends RagGenerateResult {
  history_id: number | null;
  batch_id: string;
  variations: RagHistoryEntry[];
}

/** `GET /api/rag/history`. */
export interface RagHistoryList {
  items: RagHistoryEntry[];
  total: number;
}

/** `POST /api/rag/lint`. */
export interface RagLintResponse {
  clean_prompt: string;
  warnings: string[];
  token_count: number;
  is_valid: boolean;
  archetype: 'tags' | 'short' | 'dense';
  token_counter: 'qwen' | 'heuristic';
}

export type SelectedTags = Record<string, string[]>;
