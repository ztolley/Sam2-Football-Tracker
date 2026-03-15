import type { TrackingBox, VideoAsset } from '../state/app-state';
import type { DraftBox } from './types';

export function pointerPosition(
  event: PointerEvent,
): { x: number; y: number } | null {
  const overlay = event.currentTarget as HTMLElement | null;
  if (!overlay) {
    return null;
  }

  const bounds = overlay.getBoundingClientRect();
  return {
    x: Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width),
    y: Math.min(Math.max(event.clientY - bounds.top, 0), bounds.height),
  };
}

export function draftToBox(
  draft: DraftBox,
  overlay: HTMLElement,
  activeVideo: VideoAsset,
): TrackingBox | null {
  const widthPixels = Math.abs(draft.currentX - draft.startX);
  const heightPixels = Math.abs(draft.currentY - draft.startY);
  if (widthPixels < 4 || heightPixels < 4) {
    return null;
  }

  const bounds = overlay.getBoundingClientRect();
  const left = Math.min(draft.startX, draft.currentX);
  const top = Math.min(draft.startY, draft.currentY);

  return {
    x: Math.round((left / bounds.width) * activeVideo.width),
    y: Math.round((top / bounds.height) * activeVideo.height),
    width: Math.round((widthPixels / bounds.width) * activeVideo.width),
    height: Math.round((heightPixels / bounds.height) * activeVideo.height),
  };
}

export function draftRect(draft: DraftBox): {
  left: number;
  top: number;
  width: number;
  height: number;
} {
  return {
    left: Math.min(draft.startX, draft.currentX),
    top: Math.min(draft.startY, draft.currentY),
    width: Math.abs(draft.currentX - draft.startX),
    height: Math.abs(draft.currentY - draft.startY),
  };
}
