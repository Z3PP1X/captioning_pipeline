export interface ActiveJob {
  job_id: number;
  video_id: string | number;
  video_name: string;
  chunk_id?: number | null;
  progress: number;
  step: string;
  type?: string;
}

export interface JobSSEMessage {
  type: 'connected' | 'job_queued' | 'job_started' | 'job_progress' | 'job_finished' | 'job_cancelled' | 'job_error' | 'tags_updated';
  data?: {
    job_id: number;
    video_id: string | number;
    video_name?: string;
    chunk_id?: number | null;
    progress?: number;
    step?: string;
    job_type?: string;
    type?: string;
    error?: string;
  };
}
