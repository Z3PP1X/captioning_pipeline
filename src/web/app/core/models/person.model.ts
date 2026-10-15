export interface Person {
  id: number;
  name: string;
  slug: string;
  exemplar_count: number;
  has_embedding?: boolean;
  face_crop_url?: string | null;
  video_count?: number;
  created_at?: number;
  updated_at?: number;
}

export interface TagFaceRequest {
  frame_id: number;
  person_name?: string;
  person_id?: number;
  face_index?: number;
}

export interface TagFaceResponse {
  status: string;
  person: Person;
  updated_frames: number;
}

export interface DetectedFace {
  box: [number, number, number, number];
  score: number;
  sim: number;
  matched: boolean;
}

export interface FrameFacesResponse {
  frame_id: number;
  face_count: number;
  target_person_sim: number;
  matched: boolean;
  faces: DetectedFace[];
}
