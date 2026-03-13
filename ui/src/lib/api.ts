import type { JobSummary, TrackingBox, VideoAsset } from '../state/app-state';

type TrackingSelectionPayload = {
  video_id: string;
  player_name: string;
  time_seconds: number;
  box: TrackingBox;
};

type OffscreenPayload = {
  video_id: string;
  time_seconds: number;
};

// Keep the tracker feature talking to a small REST/SSE adapter rather than
// spreading fetch details through components and actions.
export async function fetchHealth(): Promise<{ status: string; environment: string }> {
  const response = await fetch('/api/health');
  if (!response.ok) {
    throw new Error('Health check failed');
  }

  return response.json() as Promise<{ status: string; environment: string }>;
}

export async function fetchJobs(): Promise<JobSummary[]> {
  const response = await fetch('/api/v1/jobs');
  if (!response.ok) {
    throw new Error('Unable to load jobs');
  }

  return response.json() as Promise<JobSummary[]>;
}

export function streamJob(jobId: string): EventSource {
  return new EventSource(`/api/v1/jobs/${jobId}/events`);
}

export async function fetchVideos(): Promise<VideoAsset[]> {
  const response = await fetch('/api/v1/videos');
  if (!response.ok) {
    throw new Error('Unable to load videos');
  }

  return response.json() as Promise<VideoAsset[]>;
}

export async function uploadVideo(file: File): Promise<VideoAsset> {
  const formData = new FormData();
  formData.set('file', file);

  const response = await fetch('/api/v1/videos', {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error('Unable to upload video');
  }

  return response.json() as Promise<VideoAsset>;
}

export async function deleteVideo(videoId: string): Promise<void> {
  const response = await fetch(`/api/v1/videos/${videoId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error('Unable to delete video');
  }
}

export async function submitSelection(payload: TrackingSelectionPayload): Promise<JobSummary> {
  const response = await fetch('/api/v1/jobs/selection', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error('Unable to submit selection');
  }

  return response.json() as Promise<JobSummary>;
}

export async function markPlayerOffscreen(payload: OffscreenPayload): Promise<JobSummary> {
  const response = await fetch('/api/v1/jobs/offscreen', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error('Unable to mark player off-screen');
  }

  return response.json() as Promise<JobSummary>;
}

export async function renderOutput(jobId: string): Promise<JobSummary> {
  const response = await fetch(`/api/v1/jobs/${jobId}/render`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error('Unable to render output movie');
  }

  return response.json() as Promise<JobSummary>;
}
