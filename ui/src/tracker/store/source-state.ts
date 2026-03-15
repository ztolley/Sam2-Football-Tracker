import { signal } from '@lit-labs/signals';

import type { VideoAsset } from '../../state/app-state';

// Source state covers which video is active plus UI feedback for source
// management workflows such as upload and deletion.
export const selectedVideoId = signal('');
export const videos = signal<VideoAsset[]>([]);
export const playerName = signal('');
export const isUploading = signal(false);
export const isDeleting = signal(false);
export const uploadMessage = signal('');
