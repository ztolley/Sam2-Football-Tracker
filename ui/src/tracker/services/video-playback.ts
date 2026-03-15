import { isVideoPaused } from '../store';

// Media helpers stay separate from components so playback synchronization can
// be reused by source selection, selection processing, and render workflows.
async function waitForVideoMetadata(video: HTMLVideoElement): Promise<void> {
  if (video.readyState >= HTMLMediaElement.HAVE_METADATA) {
    return;
  }

  await new Promise<void>((resolve) => {
    video.addEventListener('loadedmetadata', () => resolve(), { once: true });
  });
}

async function nextFrame(): Promise<void> {
  await new Promise<void>((resolve) => {
    globalThis.requestAnimationFrame(() => resolve());
  });
}

export async function reloadVideoPreview(
  getVideoElement: () => HTMLVideoElement | null,
): Promise<void> {
  await nextFrame();
  const video = getVideoElement();
  if (!video) {
    return;
  }

  video.pause();
  video.load();
  isVideoPaused.set(true);
}

export async function syncPlaybackState(
  getVideoElement: () => HTMLVideoElement | null,
  timeSeconds: number,
  shouldPlay: boolean,
  reloadSource: boolean,
): Promise<void> {
  await nextFrame();
  const video = getVideoElement();
  if (!video) {
    return;
  }

  if (reloadSource) {
    video.load();
  }
  await waitForVideoMetadata(video);

  const maxTime = Number.isFinite(video.duration)
    ? Math.max(video.duration - 0.05, 0)
    : timeSeconds;
  try {
    video.currentTime = Math.min(Math.max(timeSeconds, 0), maxTime);
  } catch {
    video.currentTime = 0;
  }

  if (shouldPlay) {
    await video.play().catch(() => undefined);
    isVideoPaused.set(false);
    return;
  }

  video.pause();
  isVideoPaused.set(true);
}
