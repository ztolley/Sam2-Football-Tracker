import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html, unsafeCSS } from 'lit';
import { customElement } from 'lit/decorators.js';

import type { TrackingBox } from '../../../state/app-state';
import { draftToBox, draftRect, pointerPosition } from '../../geometry';
import { finishDraft, startDraft, updateDraft } from '../../actions';
import drawOverlayStyles from './tracker-draw-overlay.css?inline';
import {
  draftBox,
  getActiveJob,
  getActiveVideo,
  isDrawArmed,
  isDrawing,
  isProcessing,
  isVideoPaused,
} from '../../store';

// This overlay owns pointer interaction for drafting a player box. Keeping the
// draw state here stops the main video panel from becoming pointer-event heavy.
@customElement('tracker-draw-overlay')
export class TrackerDrawOverlay extends SignalWatcher(LitElement) {
  static styles = unsafeCSS(drawOverlayStyles);

  private emitSelectionConfirmed(box: TrackingBox): void {
    this.dispatchEvent(
      new CustomEvent<{ box: TrackingBox }>('selection-confirmed', {
        detail: { box },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private renderDisplayBox(box: TrackingBox | null) {
    const activeVideo = getActiveVideo();
    if (!box || !activeVideo) {
      return null;
    }

    const left = (box.x / activeVideo.width) * 100;
    const top = (box.y / activeVideo.height) * 100;
    const boxWidth = (box.width / activeVideo.width) * 100;
    const boxHeight = (box.height / activeVideo.height) * 100;

    return html`<div
      class="tracker-display-box"
      style=${`left:${left}%;top:${top}%;width:${boxWidth}%;height:${boxHeight}%;`}
    ></div>`;
  }

  private renderDraftBox() {
    const currentDraft = draftBox.get();
    if (!currentDraft) {
      return null;
    }

    const rect = draftRect(currentDraft);
    return html`<div
      class="tracker-draft-box"
      style=${`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;`}
    ></div>`;
  }

  private handleConfirmSelection(): void {
    const currentDraft = draftBox.get();
    const activeVideo = getActiveVideo();
    const overlay = this.renderRoot.querySelector(
      'div[data-draw-surface="true"]',
    ) as HTMLDivElement | null;
    if (!currentDraft || !activeVideo || !overlay) {
      return;
    }

    // Convert the on-screen draft rectangle back into source-video
    // coordinates before handing the selection to the tracker workflow.
    const box = draftToBox(currentDraft, overlay, activeVideo);
    if (!box) {
      this.dispatchEvent(
        new CustomEvent('selection-invalid', {
          bubbles: true,
          composed: true,
        }),
      );
      return;
    }

    this.emitSelectionConfirmed(box);
  }

  private renderDraftControls() {
    const currentDraft = draftBox.get();
    if (!currentDraft || isProcessing.get()) {
      return null;
    }

    const rect = draftRect(currentDraft);
    const buttonLeft = Math.max(12, rect.left + rect.width / 2 - 42);
    const buttonTop = rect.top + rect.height + 12;

    return html`<button
      type="button"
      data-testid="confirm-selection-button"
      class="tracker-draft-confirm"
      style=${`left:${buttonLeft}px;top:${buttonTop}px;`}
      @click=${this.handleConfirmSelection}
    >
      OK
    </button>`;
  }

  private onPointerDown(event: PointerEvent): void {
    const point = pointerPosition(event);
    if (!point) {
      return;
    }
    startDraft(point);
  }

  private onPointerMove(event: PointerEvent): void {
    const point = pointerPosition(event);
    if (!point) {
      return;
    }
    updateDraft(point);
  }

  private onPointerUp(event: PointerEvent): void {
    const point = pointerPosition(event);
    finishDraft(point ?? undefined);
  }

  render() {
    const activeJob = getActiveJob();
    const currentDraft = draftBox.get();
    const paused = isVideoPaused.get();

    return html`
      <div class="tracker-overlay-layer">
        ${this.renderDisplayBox(
          activeJob?.player_visible && !activeJob?.processed_media_url
            ? activeJob.latest_box
            : null,
        )}
        ${this.renderDraftBox()}
      </div>
      ${paused
        ? html`<div
            data-draw-surface="true"
            data-testid="draw-surface"
            class="tracker-draw-surface ${isProcessing.get()
              ? 'tracker-draw-surface--locked'
              : isDrawArmed.get() || isDrawing.get()
                ? 'tracker-draw-surface--armed'
                : 'tracker-draw-surface--locked'}"
            @pointerdown=${this.onPointerDown}
            @pointermove=${this.onPointerMove}
            @pointerup=${this.onPointerUp}
            @pointerleave=${this.onPointerUp}
          ></div>`
        : null}
      ${currentDraft ? this.renderDraftControls() : null}
    `;
  }
}
