import type { JobSummary } from '../../state/app-state';
import { streamJob } from '../../lib/api';
import type { TrackerJobUpdateHandler } from '../types';

// SSE is a better fit than tight polling here: the browser only needs
// one-way job progress updates while the server processes tracking work.
export function waitForJobCompletion(
  jobId: string,
  onUpdate: TrackerJobUpdateHandler,
): Promise<JobSummary> {
  return new Promise<JobSummary>((resolve, reject) => {
    const events = streamJob(jobId);

    const closeStream = () => {
      events.removeEventListener('job', handleJobEvent as EventListener);
      events.close();
    };

    const handleJobEvent = (event: Event) => {
      const messageEvent = event as MessageEvent<string>;
      let job: JobSummary;
      try {
        job = JSON.parse(messageEvent.data) as JobSummary;
      } catch {
        closeStream();
        reject(new Error('Received an invalid tracker status update.'));
        return;
      }

      onUpdate(job);

      if (job.status === 'failed') {
        closeStream();
        reject(new Error(job.processing_detail || 'Tracker processing failed'));
        return;
      }
      if (job.status === 'completed') {
        closeStream();
        resolve(job);
      }
    };

    events.addEventListener('job', handleJobEvent as EventListener);
    events.onerror = () => {
      if (events.readyState !== EventSource.CLOSED) {
        return;
      }
      closeStream();
      reject(new Error('Lost the tracker status stream.'));
    };
  });
}
