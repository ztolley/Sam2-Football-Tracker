import { signal } from '@lit-labs/signals';

import type { DraftBox } from '../types';

// Selection state is entirely UI-facing: draft geometry, draw mode, and the
// current validation message shown to the user.
export const draftBox = signal<DraftBox | null>(null);
export const isDrawing = signal(false);
export const isDrawArmed = signal(false);
export const errorMessage = signal('');

export function resetDraftState(): void {
  draftBox.set(null);
  isDrawing.set(false);
  isDrawArmed.set(false);
}
