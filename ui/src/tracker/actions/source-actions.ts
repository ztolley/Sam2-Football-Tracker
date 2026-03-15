import { deleteVideo, fetchHealth, fetchJobs, fetchVideos, uploadVideo } from '../../lib/api';
import { reloadVideoPreview } from '../services/video-playback';
import {
  apiStatus,
  currentTimeSeconds,
  isDeleting,
  isProcessing,
  isUploading,
  isVideoPaused,
  jobs,
  playerName,
  renderMessage,
  resetDraftState,
  selectedVideoId,
  uploadMessage,
  videos,
} from '../store';
import { setError } from './shared';

// Source actions own startup, source switching, and upload/delete workflows.
// They intentionally do not deal with drawing or job processing concerns.
export async function loadInitialTrackerState(
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  try {
    const [health, jobList, videoList] = await Promise.all([
      fetchHealth(),
      fetchJobs(),
      fetchVideos(),
    ]);
    apiStatus.set(`${health.status} (${health.environment})`);
    jobs.set(jobList);
    videos.set(videoList);
    if (!selectedVideoId.get() && videoList.length > 0) {
      selectedVideoId.set(videoList[0].id);
      await reloadVideoPreview(getVideoElement);
    }
  } catch (error) {
    apiStatus.set('offline');
    setError(error instanceof Error ? error.message : 'Unable to reach API');
  }
}

export async function selectVideo(
  videoId: string,
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  // Selecting a new source resets transient playback and draft state, but keeps
  // the rest of the app mounted so the UI feels immediate.
  selectedVideoId.set(videoId);
  resetDraftState();
  currentTimeSeconds.set(0);
  isVideoPaused.set(true);
  setError('');
  renderMessage.set('');
  await reloadVideoPreview(getVideoElement);
}

export function setPlayerName(value: string): void {
  playerName.set(value);
}

export async function uploadSelectedVideo(
  file: File,
  input: HTMLInputElement,
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  isUploading.set(true);
  uploadMessage.set('');
  setError('');
  renderMessage.set('');
  try {
    const uploadedVideo = await uploadVideo(file);
    videos.set([uploadedVideo, ...videos.get()]);
    selectedVideoId.set(uploadedVideo.id);
    uploadMessage.set(`Uploaded ${uploadedVideo.filename}`);
    resetDraftState();
    await reloadVideoPreview(getVideoElement);
  } catch (error) {
    setError(error instanceof Error ? error.message : 'Unable to upload video');
  } finally {
    isUploading.set(false);
    input.value = '';
  }
}

export async function deleteSelectedVideo(
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  const activeVideo = videos
    .get()
    .find((item) => item.id === selectedVideoId.get());
  if (!activeVideo || isDeleting.get() || isProcessing.get()) {
    return;
  }

  const confirmed = globalThis.confirm(
    `Delete ${activeVideo.filename}? This also removes any processed outputs for it.`,
  );
  if (!confirmed) {
    return;
  }

  isDeleting.set(true);
  setError('');
  uploadMessage.set('');
  renderMessage.set('');
  try {
    await deleteVideo(activeVideo.id);
    videos.set(videos.get().filter((item) => item.id !== activeVideo.id));
    jobs.set(jobs.get().filter((job) => job.video_id !== activeVideo.id));

    const remainingVideos = videos.get();
    selectedVideoId.set(remainingVideos[0]?.id ?? '');
    currentTimeSeconds.set(0);
    isVideoPaused.set(true);
    playerName.set('');
    resetDraftState();
    if (remainingVideos.length > 0) {
      await reloadVideoPreview(getVideoElement);
    }
  } catch (error) {
    setError(error instanceof Error ? error.message : 'Unable to delete video');
  } finally {
    isDeleting.set(false);
  }
}
