import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html } from 'lit';
import { customElement } from 'lit/decorators.js';
import { keyed } from 'lit/directives/keyed.js';

import './tracker-draw-overlay';
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
} from '../actions';
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
} from '../store';
import type { TrackingBox } from '../../../state/app-state';

// The video panel owns the real <video> element and the controls that depend on
// its current playback state. More specialized pointer/drawing behavior lives
// in the overlay component layered on top.
@customElement('tracker-video-panel')
export class TrackerVideoPanel extends SignalWatcher(LitElement) {
  protected createRenderRoot(): this {
    return this;
  }

  private get videoElement(): HTMLVideoElement | null {
    return this.querySelector('video[data-tracker-video="true"]');
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
      return html`<article
        class="overflow-hidden rounded-[30px] border border-cyan-400/20 bg-[#090611]/85 shadow-[0_0_0_1px_rgba(34,211,238,0.08),0_25px_70px_rgba(0,0,0,0.35)]"
      >
        <div
          class="relative aspect-video bg-[linear-gradient(135deg,_rgba(245,158,11,0.16),_transparent_28%),linear-gradient(180deg,_rgba(12,10,9,0.2),_rgba(12,10,9,0.9)),linear-gradient(120deg,_#1c1917,_#0c0a09)]"
        >
          <div
            class="absolute inset-6 rounded-[26px] border border-dashed border-stone-700/80"
          ></div>
          <div
            class="absolute right-6 bottom-6 left-6 rounded-2xl border border-cyan-400/20 bg-[#0c0d17]/78 p-4"
          >
            <p
              class="font-mono text-xs tracking-[0.25em] text-cyan-200 uppercase"
            >
              Choose a source to begin
            </p>
            <p class="mt-3 text-sm leading-6 text-fuchsia-50/74">
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

    return html`<article
      class="overflow-hidden rounded-[30px] border border-cyan-400/20 bg-[#090611]/85 shadow-[0_0_0_1px_rgba(34,211,238,0.08),0_25px_70px_rgba(0,0,0,0.35)]"
    >
      <div class="grid gap-5 p-5">
        <div
          class="relative overflow-hidden rounded-[26px] border border-fuchsia-500/25 bg-black shadow-[0_0_30px_rgba(217,70,239,0.12)]"
        >
          ${keyed(
            getPlaybackMediaUrl() || activeVideo.id,
            html`<video
              data-tracker-video="true"
              data-testid="tracker-video"
              class="${isProcessing.get() ? 'pointer-events-none' : ''} aspect-video w-full bg-black"
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
          ${showDrawButton
            ? html`<button
                type="button"
                data-testid="draw-player-button"
                class="absolute top-4 left-36 z-20 rounded-full border border-cyan-300/60 bg-cyan-300 px-4 py-2 font-['Space_Grotesk'] text-sm font-semibold text-slate-950 shadow-[0_0_24px_rgba(34,211,238,0.32)] transition hover:bg-cyan-200"
                @click=${() => armDrawMode(this.videoElement)}
              >
                ${activeJob?.latest_box ? 'Correct player' : 'Draw player'}
              </button>`
            : null}
          ${showOffscreenButton
            ? html`<button
                type="button"
                data-testid="player-left-frame-button"
                class="absolute top-4 right-4 z-20 rounded-full border border-fuchsia-300/60 bg-fuchsia-500 px-4 py-2 font-['Space_Grotesk'] text-sm font-semibold text-white shadow-[0_0_24px_rgba(217,70,239,0.4)] transition hover:bg-fuchsia-400"
                @click=${this.onPlayerOffscreen}
              >
                Player left frame
              </button>`
            : null}
          ${isProcessing.get()
            ? html`<div
                data-testid="processing-overlay"
                class="absolute inset-0 z-30 flex items-center justify-center bg-[#0a0812]/80 backdrop-blur-sm"
              >
                <div
                  class="w-[min(420px,88%)] rounded-[24px] border border-cyan-300/40 bg-[#13071f]/92 px-6 py-5 shadow-[0_0_0_1px_rgba(34,211,238,0.12),0_0_38px_rgba(217,70,239,0.12),0_20px_60px_rgba(0,0,0,0.4)]"
                >
                  <div class="flex items-center justify-between gap-4">
                    <p
                      class="font-['Space_Grotesk'] text-lg font-semibold text-fuchsia-50"
                    >
                      ${processingLabel.get()}
                    </p>
                    <span
                      class="font-mono text-sm text-cyan-200 drop-shadow-[0_0_10px_rgba(34,211,238,0.4)]"
                    >
                      ${Math.round(processingProgress.get())}%
                    </span>
                  </div>
                  <div
                    class="mt-4 h-3 overflow-hidden rounded-full bg-fuchsia-950/70"
                  >
                    <div
                      class="h-full rounded-full bg-gradient-to-r from-fuchsia-500 via-cyan-300 to-lime-300 shadow-[0_0_24px_rgba(34,211,238,0.4)] transition-[width] duration-150"
                      style=${`width:${processingProgress.get()}%;`}
                    ></div>
                  </div>
                  <p class="mt-3 text-sm text-fuchsia-50/72">
                    Playback and interaction stay locked until processing
                    completes.
                  </p>
                </div>
              </div>`
            : null}
        </div>
        <div class="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
          <div
            class="rounded-2xl border border-cyan-400/20 bg-[#0d0f1d]/85 p-4 shadow-[0_0_24px_rgba(34,211,238,0.08)]"
          >
            <p
              class="font-mono text-xs tracking-[0.25em] text-cyan-200 uppercase"
            >
              Active source
            </p>
            <h2
              class="mt-2 font-['Space_Grotesk'] text-2xl font-semibold text-fuchsia-50"
            >
              ${activeVideo.filename}
            </h2>
            <div
              class="mt-4 grid gap-2 text-sm text-fuchsia-50/74 sm:grid-cols-2"
            >
              <p>${activeVideo.width}x${activeVideo.height}</p>
              <p>${activeVideo.fps.toFixed(2)} fps</p>
              <p>${activeVideo.frame_count} frames</p>
              <p>${currentTimeSeconds.get().toFixed(2)} seconds</p>
            </div>
            <div class="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                data-testid="delete-video-button"
                class="rounded-full border border-fuchsia-300/60 bg-fuchsia-500 px-4 py-2 font-['Space_Grotesk'] text-sm font-semibold text-white shadow-[0_0_20px_rgba(217,70,239,0.24)] transition hover:bg-fuchsia-400 disabled:cursor-not-allowed disabled:opacity-60"
                ?disabled=${isProcessing.get() || isDeleting.get()}
                @click=${this.onDeleteVideo}
              >
                ${isDeleting.get() ? 'Deleting...' : 'Delete video'}
              </button>
            </div>
          </div>
          <div
            class="rounded-2xl border border-lime-300/25 bg-lime-300/10 p-4 shadow-[0_0_20px_rgba(190,242,100,0.08)]"
          >
            <p
              class="font-mono text-xs tracking-[0.25em] text-lime-200 uppercase"
            >
              Interaction
            </p>
            <p class="mt-3 text-sm leading-6 text-lime-50/90">
              Pause anywhere, use the normal controls to scrub, press
              <code>Draw player</code>, then confirm with <code>OK</code>. After
              processing, playback resumes automatically.
            </p>
            ${activeJob?.processed_media_url
              ? html`<div class="mt-4 flex flex-wrap gap-3">
                  <button
                    type="button"
                    data-testid="render-movie-button"
                    class="rounded-full border border-lime-300/70 bg-lime-300 px-4 py-2 font-['Space_Grotesk'] text-sm font-semibold text-stone-950 shadow-[0_0_24px_rgba(190,242,100,0.28)] transition hover:bg-lime-200"
                    ?disabled=${isProcessing.get()}
                    @click=${this.onRenderMovie}
                  >
                    Render movie
                  </button>
                  ${activeJob.rendered_media_url
                    ? html`<a
                        data-testid="rendered-movie-link"
                        class="rounded-full border border-cyan-300/55 bg-cyan-300/12 px-4 py-2 text-sm font-semibold text-cyan-100 shadow-[0_0_18px_rgba(34,211,238,0.12)] transition hover:bg-cyan-300/18"
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
                  class="mt-3 rounded-2xl border border-cyan-300/30 bg-cyan-300/10 px-4 py-3 text-sm text-cyan-100"
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
