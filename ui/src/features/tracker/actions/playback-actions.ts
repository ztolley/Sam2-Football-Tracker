import { currentTimeSeconds, isVideoPaused, resetDraftState } from '../store';
import { setError } from './shared';

// Playback actions keep browser media state in the shared store so other
// feature modules can react without directly owning the <video> element.
export function handleVideoPaused(currentTime: number): void {
  isVideoPaused.set(true);
  currentTimeSeconds.set(currentTime);
}

export function handleVideoPlaying(): void {
  isVideoPaused.set(false);
  resetDraftState();
}

export function handleVideoTimeUpdated(currentTime: number): void {
  currentTimeSeconds.set(currentTime);
}

export function handlePlaybackError(): void {
  setError('This video could not be shown in the browser. Try uploading it again.');
}
