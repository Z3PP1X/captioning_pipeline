import { Injectable, inject } from '@angular/core';
import { ApiService } from './api.service';
import { PromptExpandResponse } from '../models/moss.model';

@Injectable({
  providedIn: 'root',
})
export class MossService {
  private readonly api = inject(ApiService);

  async expandPrompt(
    idea: string,
    options?: {
      dataset_id?: string | null;
      trigger_word?: string;
      class_noun?: string;
      mode?: string;
      top_k?: number;
    },
  ): Promise<PromptExpandResponse> {
    const payload = {
      idea: idea.trim(),
      dataset_id: options?.dataset_id ?? null,
      trigger_word: options?.trigger_word ?? 'ohwx_subject',
      class_noun: options?.class_noun ?? 'woman',
      mode: options?.mode ?? 'CHARACTER',
      top_k: options?.top_k ?? 3,
    };

    return await this.api.post<PromptExpandResponse>('/api/rag/expand-prompt', payload);
  }
}
