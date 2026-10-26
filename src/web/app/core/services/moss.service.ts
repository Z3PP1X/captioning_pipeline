import { Injectable, inject } from '@angular/core';
import { ApiService } from './api.service';
import { PromptExpandResponse } from '../models/moss.model';

export interface ExpandPromptOptions {
  dataset_id?: string | null;
  trigger_word?: string;
  class_noun?: string;
  mode?: string;
  top_k?: number;
}

const DEFAULT_EXPAND_OPTIONS: Required<ExpandPromptOptions> = {
  dataset_id: null,
  trigger_word: 'ohwx_subject',
  class_noun: 'woman',
  mode: 'CHARACTER',
  top_k: 3,
};

@Injectable({
  providedIn: 'root',
})
export class MossService {
  private readonly api = inject(ApiService);

  async expandPrompt(
    idea: string,
    options?: ExpandPromptOptions,
  ): Promise<PromptExpandResponse> {
    const payload = {
      ...DEFAULT_EXPAND_OPTIONS,
      ...options,
      idea: idea.trim(),
    };

    return await this.api.post<PromptExpandResponse>('/api/rag/expand-prompt', payload);
  }
}
