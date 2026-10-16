/** Normalised box (0-1) relative to the frame: x/y = top-left corner. */
export interface TagBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type TagCategory =
  | 'object'
  | 'clothing'
  | 'equipment'
  | 'setting'
  | 'pose'
  | 'lighting'
  | 'style'
  | 'other';

export interface ImageTag {
  id: number;
  name: string;
  slug: string;
  category: TagCategory;
  prompt: string;
  color: string | null;
  /** Learned similarity threshold (calibrated from exemplars). */
  threshold: number;
  /** Leave-one-out accuracy on the exemplars, null until enough exemplars exist. */
  accuracy: number | null;
  positive_count: number;
  negative_count: number;
  /** Background windows learned as counter-examples around marked regions (not shown as examples). */
  context_count: number;
  confirmed_count: number;
  suggested_count: number;
  cover_url: string | null;
  created_at: number;
  updated_at: number;
}

export interface TagRegion {
  id: number;
  tag_id: number;
  frame_id: number | null;
  video_id: string | null;
  video_name?: string | null;
  polarity: 'positive' | 'negative';
  source: 'manual' | 'accepted' | 'rejected';
  box: TagBox;
  crop_url: string;
  name?: string;
  color?: string | null;
  created_at?: number;
}

/** A tag on a frame: confirmed by the user, or suggested by the model with its score and location. */
export interface FrameTag {
  tag_id: number;
  name: string;
  color: string | null;
  category?: TagCategory | null;
  score: number | null;
  box: TagBox | null;
}

export interface FrameTagsResponse {
  frame_id: number;
  video_id: string;
  confirmed: FrameTag[];
  rejected: FrameTag[];
  suggestions: FrameTag[];
  regions: TagRegion[];
}

/** Compact tag reference carried by curation frames (`CandidateFrame.tags`). */
export interface CandidateTag {
  tag_id: number;
  name: string;
  color: string | null;
  status: 'confirmed' | 'suggested';
  score: number | null;
}

export interface TagLearnResponse {
  status: string;
  tag_id: number;
  frame_id?: number;
  video_id?: string;
  region_id?: number;
  threshold?: number;
  accuracy?: number | null;
  tag: ImageTag | null;
  frame: FrameTagsResponse | null;
}

export interface VideoTagSummary {
  tag_id: number;
  name: string;
  color: string | null;
  category: TagCategory;
  confirmed: number;
  suggested: number;
  rejected: number;
}
