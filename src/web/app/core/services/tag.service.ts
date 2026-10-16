import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import {
  FrameTagsResponse,
  ImageTag,
  TagBox,
  TagCategory,
  TagLearnResponse,
  TagRegion,
  VideoTagSummary,
} from '../models/tag.model';

/**
 * Learn-as-you-tag image tagging: the tag vocabulary, the tags of the frame open in the lightbox, and the
 * region-marking state shared by the lightbox overlay and the tag panel.
 */
@Injectable({
  providedIn: 'root',
})
export class TagService {
  private readonly api = inject(ApiService);

  readonly tags = signal<ImageTag[]>([]);
  readonly categories = signal<TagCategory[]>([
    'object',
    'clothing',
    'equipment',
    'setting',
    'pose',
    'lighting',
    'style',
    'other',
  ]);
  readonly loading = signal(false);

  /** Tags of the frame shown in the lightbox (confirmed, rejected, live suggestions, marked regions). */
  readonly frameTags = signal<FrameTagsResponse | null>(null);
  readonly frameLoading = signal(false);
  /** True while the user drags a box on the lightbox image. */
  readonly drawMode = signal(false);
  /** Box drawn by the user, waiting for a tag name. */
  readonly pendingBox = signal<TagBox | null>(null);
  /** Tag highlighted on the image (hover on a chip or suggestion). */
  readonly highlightTagId = signal<number | null>(null);
  readonly busy = signal(false);
  private currentFrameId: number | null = null;

  readonly tagsByName = computed(() => {
    const map = new Map<string, ImageTag>();
    for (const t of this.tags()) map.set(t.name.toLowerCase(), t);
    return map;
  });

  async loadTags(): Promise<ImageTag[]> {
    this.loading.set(true);
    try {
      const res = await this.api.get<{ tags: ImageTag[]; categories: TagCategory[] }>('/api/tags');
      this.tags.set(res.tags || []);
      if (res.categories?.length) this.categories.set(res.categories);
      return res.tags || [];
    } finally {
      this.loading.set(false);
    }
  }

  /** Tag names matching a typed prefix/substring, best matches first. */
  matchTags(query: string, limit = 8): ImageTag[] {
    const q = query.trim().toLowerCase();
    const list = this.tags();
    if (!q) return list.slice(0, limit);
    return list
      .filter((t) => t.name.toLowerCase().includes(q))
      .sort((a, b) => {
        const ap = a.name.toLowerCase().startsWith(q) ? 0 : 1;
        const bp = b.name.toLowerCase().startsWith(q) ? 0 : 1;
        return ap - bp || b.confirmed_count - a.confirmed_count;
      })
      .slice(0, limit);
  }

  async loadFrameTags(frameId: number): Promise<FrameTagsResponse | null> {
    this.frameLoading.set(true);
    try {
      const res = await this.api.get<FrameTagsResponse>(`/api/frames/${frameId}/tags`);
      // Ignore a response for a frame the user has already navigated away from
      if (this.currentFrameId === frameId) this.frameTags.set(res);
      return res;
    } catch (err) {
      if (this.currentFrameId === frameId) this.frameTags.set(null);
      throw err;
    } finally {
      this.frameLoading.set(false);
    }
  }

  setCurrentFrame(frameId: number | null): void {
    if (this.currentFrameId === frameId) return;
    this.currentFrameId = frameId;
    this.frameTags.set(null);
    this.pendingBox.set(null);
    this.drawMode.set(false);
    this.highlightTagId.set(null);
    if (frameId !== null) {
      this.loadFrameTags(frameId).catch((err) => console.warn('Failed to load frame tags:', err));
    }
  }

  private async learn(request: Promise<TagLearnResponse>): Promise<TagLearnResponse> {
    this.busy.set(true);
    try {
      const res = await request;
      if (res.frame && res.frame.frame_id === this.currentFrameId) this.frameTags.set(res.frame);
      await this.loadTags();
      return res;
    } finally {
      this.busy.set(false);
    }
  }

  /** Mark a region as showing a tag (existing id, or a new tag by name). */
  markRegion(
    frameId: number,
    box: TagBox,
    tag: { tagId?: number; tagName?: string; category?: TagCategory },
    polarity: 'positive' | 'negative' = 'positive',
  ): Promise<TagLearnResponse> {
    this.pendingBox.set(null);
    this.drawMode.set(false);
    return this.learn(
      this.api.post<TagLearnResponse>(`/api/frames/${frameId}/tags`, {
        box,
        tag_id: tag.tagId ?? null,
        tag_name: tag.tagName ?? null,
        category: tag.category ?? 'object',
        polarity,
      }),
    );
  }

  acceptSuggestion(frameId: number, tagId: number, box?: TagBox | null): Promise<TagLearnResponse> {
    return this.learn(
      this.api.post<TagLearnResponse>(`/api/frames/${frameId}/tags/${tagId}/accept`, box ? { box } : {}),
    );
  }

  rejectSuggestion(frameId: number, tagId: number, box?: TagBox | null): Promise<TagLearnResponse> {
    return this.learn(
      this.api.post<TagLearnResponse>(`/api/frames/${frameId}/tags/${tagId}/reject`, box ? { box } : {}),
    );
  }

  removeFrameTag(frameId: number, tagId: number): Promise<TagLearnResponse> {
    return this.learn(this.api.delete<TagLearnResponse>(`/api/frames/${frameId}/tags/${tagId}`));
  }

  async createTag(name: string, category: TagCategory = 'object'): Promise<ImageTag> {
    const res = await this.api.post<{ tag: ImageTag }>('/api/tags', { name, category });
    await this.loadTags();
    return res.tag;
  }

  async updateTag(
    tagId: number,
    patch: Partial<Pick<ImageTag, 'name' | 'category' | 'prompt' | 'color'>>,
  ): Promise<ImageTag> {
    const res = await this.api.patch<{ tag: ImageTag }>(`/api/tags/${tagId}`, patch);
    await this.loadTags();
    return res.tag;
  }

  async deleteTag(tagId: number): Promise<void> {
    await this.api.delete(`/api/tags/${tagId}`);
    await this.loadTags();
  }

  async mergeTag(sourceId: number, intoTagId: number): Promise<ImageTag> {
    const res = await this.api.post<{ tag: ImageTag }>(`/api/tags/${sourceId}/merge`, { into_tag_id: intoTagId });
    await this.loadTags();
    return res.tag;
  }

  async getTagDetail(tagId: number): Promise<{ tag: ImageTag; regions: TagRegion[] }> {
    return this.api.get<{ tag: ImageTag; regions: TagRegion[] }>(`/api/tags/${tagId}`);
  }

  async deleteRegion(regionId: number): Promise<TagLearnResponse> {
    return this.learn(this.api.delete<TagLearnResponse>(`/api/tag-regions/${regionId}`));
  }

  async suggestForVideo(videoId: string | number): Promise<{ job_id: number; status: string }> {
    return this.api.post<{ job_id: number; status: string }>(`/api/videos/${videoId}/suggest-tags`, {});
  }

  async getVideoSummary(videoId: string | number): Promise<VideoTagSummary[]> {
    const res = await this.api.get<{ tags: VideoTagSummary[] }>(`/api/videos/${videoId}/tags`);
    return res.tags || [];
  }

  refreshCurrentFrame(): void {
    if (this.currentFrameId !== null) {
      this.loadFrameTags(this.currentFrameId).catch(() => undefined);
    }
  }
}
