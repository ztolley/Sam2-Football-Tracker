// These types mirror the API payloads. Keeping them in one place makes it easy
// to see the contract between the Lit frontend and the FastAPI backend.
export type TrackingBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type TrackingAction = {
  kind: 'selection' | 'offscreen';
  time_seconds: number;
  box: TrackingBox | null;
  created_at: string;
};

export type JobSummary = {
  id: string;
  video_id: string;
  video_filename: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  source_path: string;
  player_name: string;
  created_at: string;
  updated_at: string;
  progress_percent: number;
  processing_detail: string;
  latest_time_seconds: number | null;
  latest_box: TrackingBox | null;
  player_visible: boolean;
  processed_media_url: string | null;
  rendered_media_url: string | null;
  actions: TrackingAction[];
};

export type VideoAsset = {
  id: string;
  filename: string;
  stored_name: string;
  content_type: string;
  size_bytes: number;
  width: number;
  height: number;
  fps: number;
  duration_seconds: number;
  frame_count: number;
  created_at: string;
  source_path: string;
  media_url: string;
};
