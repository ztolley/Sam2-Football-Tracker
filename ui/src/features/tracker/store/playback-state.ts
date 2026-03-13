import { signal } from '@lit-labs/signals';

// Playback state mirrors only the values that other feature workflows need to
// react to; the DOM video element remains the source of truth for media APIs.
export const apiStatus = signal('checking');
export const currentTimeSeconds = signal(0);
export const isVideoPaused = signal(true);
