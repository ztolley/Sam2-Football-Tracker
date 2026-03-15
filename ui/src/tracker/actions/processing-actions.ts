import type { JobSummary } from '../../state/app-state';
import { markPlayerOffscreen, renderOutput } from '../../lib/api';
import { waitForJobCompletion } from '../services/job-status-stream';
import { syncPlaybackState } from '../services/video-playback';
import {
  applyJobProgress,
  currentTimeSeconds,
  getActiveJob,
  getActiveVideo,
  getPlaybackMediaUrl,
  isProcessing,
  processingLabel,
  processingProgress,
  renderMessage,
} from '../store';
import { setError } from './shared';

// Processing actions cover follow-up server work after the tracker already has
// an active player: off-screen markers and final render requests.
export async function markSelectedPlayerOffscreen(
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  const activeVideo = getActiveVideo();
  const activeJob = getActiveJob();
  if (!activeVideo || !activeJob) {
    return;
  }

  const video = getVideoElement();
  const resumeTime = video?.currentTime ?? currentTimeSeconds.get();
  const previousMediaUrl = getPlaybackMediaUrl();
  video?.pause();
  isProcessing.set(true);
  processingProgress.set(2);
  processingLabel.set('Updating off-screen state...');
  setError('');
  renderMessage.set('');
  try {
    const job = await markPlayerOffscreen({
      video_id: activeVideo.id,
      time_seconds: video?.currentTime ?? currentTimeSeconds.get(),
    });
    applyJobProgress(job);
    const completedJob = await waitForJobCompletion(job.id, applyJobProgress);
    await syncPlaybackState(
      getVideoElement,
      resumeTime,
      true,
      completedJob.processed_media_url !== previousMediaUrl,
    );
  } catch (error) {
    setError(
      error instanceof Error
        ? error.message
        : 'Unable to mark player off-screen',
    );
  } finally {
    isProcessing.set(false);
  }
}

export async function renderSelectedMovie(
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  const activeJob = getActiveJob();
  if (!activeJob) {
    return;
  }

  const video = getVideoElement();
  const resumeTime = video?.currentTime ?? currentTimeSeconds.get();
  video?.pause();
  isProcessing.set(true);
  processingProgress.set(2);
  processingLabel.set('Rendering movie...');
  setError('');
  renderMessage.set('');
  try {
    const job = await renderOutput(activeJob.id);
    applyJobProgress(job);
    const completedJob: JobSummary = await waitForJobCompletion(
      job.id,
      applyJobProgress,
    );
    renderMessage.set(
      completedJob.rendered_media_url
        ? 'Rendered movie ready.'
        : 'Movie render completed.',
    );
    await syncPlaybackState(getVideoElement, resumeTime, false, false);
  } catch (error) {
    setError(error instanceof Error ? error.message : 'Unable to render movie');
  } finally {
    isProcessing.set(false);
  }
}
