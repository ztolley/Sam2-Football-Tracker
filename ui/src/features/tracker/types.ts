import type { JobSummary } from '../../state/app-state';

export type DraftBox = {
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
};

export type TrackerJobUpdateHandler = (job: JobSummary) => void;
