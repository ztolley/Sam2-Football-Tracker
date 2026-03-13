import { signal } from '@lit-labs/signals';

import type { JobSummary } from '../../../state/app-state';

// Processing state tracks the current backend job snapshot and the overlay
// labels derived from it while the user waits for work to complete.
export const jobs = signal<JobSummary[]>([]);
export const isProcessing = signal(false);
export const processingLabel = signal('Processing...');
export const processingProgress = signal(0);
export const renderMessage = signal('');

export function upsertJob(job: JobSummary): void {
  const remaining = jobs.get().filter((candidate) => candidate.id !== job.id);
  jobs.set([job, ...remaining]);
}

export function applyJobProgress(job: JobSummary): void {
  upsertJob(job);
  processingProgress.set(job.progress_percent);
  processingLabel.set(job.processing_detail);
}
