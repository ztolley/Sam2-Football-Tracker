import type { TrackingBox } from '../../state/app-state';
import { waitForJobCompletion } from '../services/job-status-stream';
import { syncPlaybackState } from '../services/video-playback';
import {
  applyJobProgress,
  currentTimeSeconds,
  draftBox,
  getActiveJob,
  getActiveVideo,
  getPlaybackMediaUrl,
  isDrawArmed,
  isDrawing,
  isProcessing,
  isVideoPaused,
  playerName,
  processingLabel,
  processingProgress,
  renderMessage,
  resetDraftState,
} from '../store';
import { submitSelection } from '../../lib/api';
import { setError } from './shared';

// Selection actions translate local draw gestures into tracker prompts and then
// hand off to the backend processing flow.
export function setSelectionError(): void {
  setError('Draw a larger selection before confirming.');
}

export function armDrawMode(video: HTMLVideoElement | null): void {
  if (isProcessing.get()) {
    return;
  }

  video?.pause();
  isDrawArmed.set(true);
  setError('');
  renderMessage.set('');
}

export function startDraft(point: { x: number; y: number }): void {
  if (!isVideoPaused.get() || isProcessing.get() || !isDrawArmed.get()) {
    return;
  }

  setError('');
  isDrawing.set(true);
  draftBox.set({
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
  });
}

export function updateDraft(point: { x: number; y: number }): void {
  const currentDraft = draftBox.get();
  if (!isDrawing.get() || !currentDraft) {
    return;
  }

  draftBox.set({
    ...currentDraft,
    currentX: point.x,
    currentY: point.y,
  });
}

export function finishDraft(point?: { x: number; y: number }): void {
  const currentDraft = draftBox.get();
  if (!isDrawing.get() || !currentDraft) {
    return;
  }

  isDrawing.set(false);
  draftBox.set({
    ...currentDraft,
    currentX: point?.x ?? currentDraft.currentX,
    currentY: point?.y ?? currentDraft.currentY,
  });
  isDrawArmed.set(false);
}

export async function confirmSelection(
  getVideoElement: () => HTMLVideoElement | null,
  box: TrackingBox,
): Promise<void> {
  const activeVideo = getActiveVideo();
  if (!activeVideo) {
    return;
  }

  const video = getVideoElement();
  const resumeTime = video?.currentTime ?? currentTimeSeconds.get();
  const previousMediaUrl = getPlaybackMediaUrl();
  video?.pause();
  // Lock interaction immediately so the UI matches the server-side job state.
  isProcessing.set(true);
  processingProgress.set(2);
  processingLabel.set(
    getActiveJob()?.latest_box ? 'Starting correction...' : 'Starting tracker...',
  );
  setError('');
  renderMessage.set('');
  try {
    const job = await submitSelection({
      video_id: activeVideo.id,
      player_name: playerName.get().trim() || 'selected player',
      time_seconds: video?.currentTime ?? currentTimeSeconds.get(),
      box,
    });
    applyJobProgress(job);
    const completedJob = await waitForJobCompletion(job.id, applyJobProgress);
    resetDraftState();
    if (completedJob.player_visible) {
      await syncPlaybackState(
        getVideoElement,
        resumeTime,
        true,
        completedJob.processed_media_url !== previousMediaUrl,
      );
    }
  } catch (error) {
    setError(error instanceof Error ? error.message : 'Unable to submit selection');
  } finally {
    isProcessing.set(false);
  }
}
