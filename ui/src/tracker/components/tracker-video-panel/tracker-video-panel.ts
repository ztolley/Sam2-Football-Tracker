import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html, unsafeCSS } from 'lit';
import { customElement } from 'lit/decorators.js';
import { keyed } from 'lit/directives/keyed.js';

import '../tracker-draw-overlay/tracker-draw-overlay';
import buttonsStyles from '../../../styles/buttons.css?inline';
import feedbackStyles from '../../../styles/feedback.css?inline';
import layoutStyles from '../../../styles/layout.css?inline';
import surfacesStyles from '../../../styles/surfaces.css?inline';
import {
  armDrawMode,
  confirmSelection,
  deleteSelectedVideo,
  handlePlaybackError,
  handleVideoPaused,
  handleVideoPlaying,
  handleVideoTimeUpdated,
  markSelectedPlayerOffscreen,
  renderSelectedMovie,
  setSelectionError,
} from '../../actions';
import {
  currentTimeSeconds,
  draftBox,
  getActiveJob,
  getActiveVideo,
  getPlaybackMediaUrl,
  isDeleting,
  isProcessing,
  isVideoPaused,
  processingLabel,
  processingProgress,
  renderMessage,
} from '../../store';
import type { TrackingBox } from '../../../state/app-state';
import videoPanelStyles from './tracker-video-panel.css?inline';

// The video panel owns the real <video> element and the controls that depend on
// its current playback state. More specialized pointer/drawing behavior lives
// in the overlay component layered on top.
@customElement('tracker-video-panel')
export class TrackerVideoPanel extends SignalWatcher(LitElement) {
  static styles = [
    unsafeCSS(layoutStyles),
    unsafeCSS(buttonsStyles),
    unsafeCSS(feedbackStyles),
    unsafeCSS(surfacesStyles),
    unsafeCSS(videoPanelStyles),
  ];

  private get videoElement(): HTMLVideoElement | null {
    return this.renderRoot.querySelector('video[data-tracker-video="true"]');
  }

  private async onDeleteVideo(): Promise<void> {
    await deleteSelectedVideo(() => this.videoElement);
  }

  private async onPlayerOffscreen(): Promise<void> {
    await markSelectedPlayerOffscreen(() => this.videoElement);
  }

  private async onRenderMovie(): Promise<void> {
    await renderSelectedMovie(() => this.videoElement);
  }

  private onVideoPause(event: Event): void {
    const video = event.currentTarget as HTMLVideoElement;
    handleVideoPaused(video.currentTime);
  }

  private onVideoLoadedData(event: Event): void {
    const video = event.currentTarget as HTMLVideoElement;
    if (!Number.isFinite(video.duration) || video.duration <= 0) {
      return;
    }
    if (video.currentTime > 0 || !video.paused) {
      return;
    }

    const previewTime = Math.min(0.001, video.duration / 2);
    if (previewTime <= 0) {
      return;
    }

    try {
      // Nudging to a real timestamp helps some browsers paint the first frame
      // for freshly selected uploads instead of showing a black panel.
      video.currentTime = previewTime;
    } catch {
      // Leave the browser's default frame handling in place.
    }
  }

  private onVideoPlay(): void {
    if (isProcessing.get()) {
      this.videoElement?.pause();
      return;
    }
    handleVideoPlaying();
  }

  private onVideoTimeUpdate(event: Event): void {
    const video = event.currentTarget as HTMLVideoElement;
    handleVideoTimeUpdated(video.currentTime);
  }

  private onSelectionInvalid(): void {
    setSelectionError();
  }

  private async onSelectionConfirmed(
    event: CustomEvent<{ box: TrackingBox }>,
  ): Promise<void> {
    await confirmSelection(() => this.videoElement, event.detail.box);
  }

  render() {
    const activeVideo = getActiveVideo();
    const activeJob = getActiveJob();

    if (!activeVideo) {
      return html`<article class="tracker-panel">
        <div class="tracker-empty-state">
          <div class="tracker-empty-state-frame"></div>
          <div class="tracker-empty-state-copy">
            <p class="tracker-eyebrow">
              Choose a source to begin
            </p>
            <p class="tracker-text tracker-copy-spacing">
              Select an uploaded video or add a new one from the panel on the
              right.
            </p>
          </div>
        </div>
      </article>`;
    }

    const showDrawButton =
      isVideoPaused.get() && !isProcessing.get() && !draftBox.get();
    const showOffscreenButton =
      isVideoPaused.get() &&
      !draftBox.get() &&
      !isProcessing.get() &&
      Boolean(activeJob?.player_visible);

    return html`<article class="tracker-panel">
      <div class="tracker-panel-body">
        <div class="tracker-video-frame">
          ${keyed(
            getPlaybackMediaUrl() || activeVideo.id,
            html`<video
              data-tracker-video="true"
              data-testid="tracker-video"
              class="tracker-video-element ${isProcessing.get()
                ? 'tracker-video-element--locked'
                : ''}"
              ?controls=${!isProcessing.get()}
              playsinline
              preload="auto"
              src=${getPlaybackMediaUrl()}
              @error=${handlePlaybackError}
              @loadeddata=${this.onVideoLoadedData}
              @pause=${this.onVideoPause}
              @play=${this.onVideoPlay}
              @timeupdate=${this.onVideoTimeUpdate}
            ></video>`,
          )}
          <tracker-draw-overlay
            @selection-confirmed=${this.onSelectionConfirmed}
            @selection-invalid=${this.onSelectionInvalid}
          ></tracker-draw-overlay>
          ${showDrawButton || showOffscreenButton
            ? html`<div class="tracker-floating-actions">
                ${showDrawButton
                  ? html`<button
                      type="button"
                      data-testid="draw-player-button"
                      class="tracker-button tracker-button--cyan"
                      @click=${() => armDrawMode(this.videoElement)}
                    >
                      ${activeJob?.latest_box ? 'Correct player' : 'Draw player'}
                    </button>`
                  : null}
                ${showOffscreenButton
                  ? html`<button
                      type="button"
                      data-testid="player-left-frame-button"
                      class="tracker-button tracker-button--fuchsia"
                      @click=${this.onPlayerOffscreen}
                    >
                      Player left frame
                    </button>`
                  : null}
              </div>`
            : null}
          ${isProcessing.get()
            ? html`<div
                data-testid="processing-overlay"
                class="tracker-processing-overlay"
              >
                <div class="tracker-processing-modal">
                  <div class="tracker-processing-header">
                    <p class="tracker-processing-title">${processingLabel.get()}</p>
                    <span class="tracker-processing-value">
                      ${Math.round(processingProgress.get())}%
                    </span>
                  </div>
                  <div class="tracker-progress-track">
                    <div
                      class="tracker-progress-bar"
                      style=${`width:${processingProgress.get()}%;`}
                    ></div>
                  </div>
                  <p class="tracker-text tracker-copy-spacing">
                    Playback and interaction stay locked until processing
                    completes.
                  </p>
                </div>
              </div>`
            : null}
        </div>
        <div class="tracker-section-grid">
          <div class="tracker-card">
            <p class="tracker-eyebrow">
              Active source
            </p>
            <h2 class="tracker-subtitle">${activeVideo.filename}</h2>
            <div class="tracker-meta-grid">
              <p>${activeVideo.width}x${activeVideo.height}</p>
              <p>${activeVideo.fps.toFixed(2)} fps</p>
              <p>${activeVideo.frame_count} frames</p>
              <p>${currentTimeSeconds.get().toFixed(2)} seconds</p>
            </div>
            <div class="tracker-inline-actions">
              <button
                type="button"
                data-testid="delete-video-button"
                class="tracker-button tracker-button--fuchsia tracker-button--delete"
                ?disabled=${isProcessing.get() || isDeleting.get()}
                @click=${this.onDeleteVideo}
              >
                ${isDeleting.get() ? 'Deleting...' : 'Delete video'}
              </button>
            </div>
          </div>
          <div class="tracker-card tracker-card--accent">
            <p class="tracker-eyebrow tracker-eyebrow--lime">
              Interaction
            </p>
            <p class="tracker-text tracker-text--bright tracker-copy-spacing">
              Pause anywhere, use the normal controls to scrub, press
              <code>Draw player</code>, then confirm with <code>OK</code>. After
              processing, playback resumes automatically.
            </p>
            ${activeJob?.processed_media_url
              ? html`<div class="tracker-inline-actions">
                  <button
                    type="button"
                    data-testid="render-movie-button"
                    class="tracker-button tracker-button--lime"
                    ?disabled=${isProcessing.get()}
                    @click=${this.onRenderMovie}
                  >
                    Render movie
                  </button>
                  ${activeJob.rendered_media_url
                    ? html`<a
                        data-testid="rendered-movie-link"
                        class="tracker-link-button"
                        href=${activeJob.rendered_media_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open rendered movie
                      </a>`
                    : null}
                </div>`
              : null}
            ${renderMessage.get()
              ? html`<p
                  class="tracker-message tracker-message--info tracker-copy-spacing"
                >
                  ${renderMessage.get()}
                </p>`
              : null}
          </div>
        </div>
      </div>
    </article>`;
  }
}
