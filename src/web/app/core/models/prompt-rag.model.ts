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
  equipment_text?: string;
  surroundings_text?: string;
  position_text?: string;
  lighting_text?: string;
}

export interface RagRetrieveResponse {
  results: RagItemPreview[];
  index_ready: boolean;
}

export type RagBuildState = 'idle' | 'running' | 'done' | 'error';

/** `GET /api/rag/status` and `POST /api/rag/index`. */
export interface RagIndexStatus {
  ready: boolean;
  indexed: number;
  index: {
    count: number;
    source: 'curated' | 'finished_merged' | null;
    source_path: string | null;
    built_at: number;
    embedding_model: string | null;
  } | null;
  /** null while a build is running (folder scan skipped). */
  sources: {
    source: 'curated' | 'finished_merged' | null;
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
}

export interface RagGenerateRequest {
  selected_tags: Record<string, string[]>;
  user_description: string;
  character?: string | null;
  model: string;
  trigger: string;
  top_k?: number;
  local_url?: string;
  api_key?: string;
}

export interface RagGenerateResponse {
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

export type SelectedTags = Record<string, string[]>;
