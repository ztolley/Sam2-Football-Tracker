import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html } from 'lit';
import { customElement } from 'lit/decorators.js';

import type { TrackingBox } from '../../../state/app-state';
import { draftToBox, draftRect, pointerPosition } from '../geometry';
import { finishDraft, startDraft, updateDraft } from '../actions';
import {
  draftBox,
  getActiveJob,
  getActiveVideo,
  isDrawArmed,
  isDrawing,
  isProcessing,
  isVideoPaused,
} from '../store';

// This overlay owns pointer interaction for drafting a player box. Keeping the
// draw state here stops the main video panel from becoming pointer-event heavy.
@customElement('tracker-draw-overlay')
export class TrackerDrawOverlay extends SignalWatcher(LitElement) {
  protected createRenderRoot(): this {
    return this;
  }

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
      class="absolute border-2 border-cyan-200 bg-cyan-300/10 shadow-[0_0_0_1px_rgba(34,211,238,0.35),0_0_28px_rgba(34,211,238,0.24)]"
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
      class="absolute border-2 border-fuchsia-300 bg-fuchsia-400/10 shadow-[0_0_24px_rgba(232,121,249,0.28)]"
      style=${`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;`}
    ></div>`;
  }

  private handleConfirmSelection(): void {
    const currentDraft = draftBox.get();
    const activeVideo = getActiveVideo();
    const overlay = this.querySelector(
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
      class="absolute z-20 rounded-full border border-lime-300/70 bg-lime-300 px-5 py-2 font-['Space_Grotesk'] text-sm font-semibold text-stone-950 shadow-[0_0_24px_rgba(190,242,100,0.45),0_14px_30px_rgba(0,0,0,0.35)] transition hover:bg-lime-200"
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
      <div class="pointer-events-none absolute inset-0">
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
            class="${isProcessing.get()
              ? 'pointer-events-none'
              : isDrawArmed.get() || isDrawing.get()
                ? 'cursor-crosshair'
                : 'pointer-events-none'} absolute inset-0"
            @pointerdown=${this.onPointerDown}
            @pointermove=${this.onPointerMove}
            @pointerup=${this.onPointerUp}
            @pointerleave=${this.onPointerUp}
          ></div>`
        : null}
      ${paused
        ? html`<div
            class="pointer-events-none absolute top-4 left-4 z-10 rounded-full bg-stone-950/80 px-4 py-2 font-mono text-xs tracking-[0.2em] text-stone-100 uppercase"
          >
            <span
              class="text-cyan-200 drop-shadow-[0_0_10px_rgba(34,211,238,0.55)]"
            >
              ${isDrawArmed.get()
                ? activeJob?.latest_box
                  ? 'Draw correction'
                  : 'Draw player'
                : 'Paused'}
            </span>
          </div>`
        : null}
      ${currentDraft ? this.renderDraftControls() : null}
    `;
  }
}
