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

export interface RagItemPreview {
  id: number;
  stem: string;
  score: number;
  matched_tags: string[];
  img_rel_path: string | null;
  full_caption: string;
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
}

export interface RagGenerateResponse {
  model: string;
  trigger: string;
  character?: string | null;
  archetypes: {
    a: PromptArchetype;
    b: PromptArchetype;
    c: PromptArchetype;
  };
  rag_context: RagItemPreview[];
}

export type SelectedTags = Record<string, string[]>;
