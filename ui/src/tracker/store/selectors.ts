import type { JobSummary, VideoAsset } from '../../state/app-state';
import { jobs } from './processing-state';
import { selectedVideoId, videos } from './source-state';

// Selectors centralize the relationships between source state and job state so
// components do not need to know how those collections are keyed.
export function getActiveVideo(): VideoAsset | null {
  return videos.get().find((video) => video.id === selectedVideoId.get()) ?? null;
}

export function getActiveJob(): JobSummary | null {
  return jobs.get().find((job) => job.video_id === selectedVideoId.get()) ?? null;
}

export function getPlaybackMediaUrl(): string {
  return getActiveJob()?.processed_media_url ?? getActiveVideo()?.media_url ?? '';
}
