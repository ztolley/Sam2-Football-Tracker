import { errorMessage } from '../store';

// Shared UI actions are kept minimal on purpose so feature actions still read
// as the primary workflow entry points.
export function setError(message: string): void {
  errorMessage.set(message);
}
