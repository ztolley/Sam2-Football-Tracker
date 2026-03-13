import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html } from 'lit';
import { customElement } from 'lit/decorators.js';

import { selectVideo, setPlayerName, uploadSelectedVideo } from '../actions';
import {
  errorMessage,
  isUploading,
  playerName,
  selectedVideoId,
  uploadMessage,
  videos,
} from '../store';

// The sidebar is intentionally focused on source selection and lightweight
// metadata entry. It should stay free of playback and drawing logic.
@customElement('tracker-sidebar')
export class TrackerSidebar extends SignalWatcher(LitElement) {
  protected createRenderRoot(): this {
    return this;
  }

  private get videoElement(): HTMLVideoElement | null {
    return document.querySelector('video[data-tracker-video="true"]');
  }

  private async handleVideoSelected(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLSelectElement;
    await selectVideo(input.value, () => this.videoElement);
  }

  private async handleUploadSelected(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }

    await uploadSelectedVideo(file, input, () => this.videoElement);
  }

  private handlePlayerNameInput(event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    setPlayerName(input.value);
  }

  render() {
    return html`<aside class="grid gap-6">
      <section
        class="rounded-[28px] border border-fuchsia-500/20 bg-[#12071f]/82 p-5 shadow-[0_0_24px_rgba(217,70,239,0.08)]"
      >
        <div class="mb-4">
          <h2
            class="mt-2 font-['Space_Grotesk'] text-2xl font-semibold text-cyan-200"
          >
            Video
          </h2>
        </div>
        <div class="grid gap-4">
          <label class="grid gap-2 text-sm text-fuchsia-50/80">
            Stored videos
            <select
              data-testid="stored-video-select"
              class="rounded-2xl border border-fuchsia-400/20 bg-[#0b0e18] px-4 py-3 text-fuchsia-50 transition outline-none focus:border-cyan-300 focus:shadow-[0_0_0_3px_rgba(34,211,238,0.12)]"
              @change=${this.handleVideoSelected}
            >
              <option value="" ?selected=${!selectedVideoId.get()}>
                Select a video
              </option>
              ${videos.get().map(
                (video) => html`
                  <option
                    value=${video.id}
                    ?selected=${video.id === selectedVideoId.get()}
                  >
                    ${video.filename} · ${video.width}x${video.height}
                  </option>
                `,
              )}
            </select>
          </label>
          <div class="flex items-center gap-3">
            <div class="h-px flex-1 bg-fuchsia-500/20"></div>
            <span
              class="font-mono text-xs tracking-[0.35em] text-cyan-200/70 uppercase"
              >or</span
            >
            <div class="h-px flex-1 bg-fuchsia-500/20"></div>
          </div>
          <label class="grid gap-2 text-sm text-fuchsia-50/80">
            Upload new video
            <input
              data-testid="upload-video-input"
              type="file"
              accept="video/*"
              class="rounded-2xl border border-dashed border-cyan-300/25 bg-[#0b0e18] px-4 py-3 text-sm text-fuchsia-50/80 file:mr-4 file:rounded-xl file:border-0 file:bg-cyan-300 file:px-3 file:py-2 file:font-['Space_Grotesk'] file:font-semibold file:text-slate-950"
              ?disabled=${isUploading.get()}
              @change=${this.handleUploadSelected}
            />
          </label>
          ${uploadMessage.get()
            ? html`<p
                class="rounded-2xl border border-lime-300/30 bg-lime-300/10 px-4 py-3 text-sm text-lime-100"
              >
                ${uploadMessage.get()}
              </p>`
            : null}
        </div>
      </section>

      <section
        class="rounded-[28px] border border-cyan-400/20 bg-[#07111d]/82 p-5 shadow-[0_0_24px_rgba(34,211,238,0.08)]"
      >
        <div class="grid gap-4">
          <label class="grid gap-2 text-sm text-cyan-50/80">
            Player name
            <input
              class="rounded-2xl border border-cyan-300/20 bg-[#09101b] px-4 py-3 text-cyan-50 transition outline-none focus:border-fuchsia-300 focus:shadow-[0_0_0_3px_rgba(217,70,239,0.12)]"
              .value=${playerName.get()}
              @input=${this.handlePlayerNameInput}
              placeholder="Enter player name here"
            />
          </label>
          ${errorMessage.get()
            ? html`<p
                class="rounded-2xl border border-fuchsia-400/35 bg-fuchsia-500/12 px-4 py-3 text-sm text-fuchsia-100"
              >
                ${errorMessage.get()}
              </p>`
            : html``}
        </div>
      </section>
    </aside>`;
  }
}
