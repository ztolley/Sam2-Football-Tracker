import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html, unsafeCSS } from 'lit';
import { customElement } from 'lit/decorators.js';

import feedbackStyles from '../../../styles/feedback.css?inline';
import formsStyles from '../../../styles/forms.css?inline';
import { selectVideo, setPlayerName, uploadSelectedVideo } from '../../actions';
import sidebarStyles from './tracker-sidebar.css?inline';
import {
  errorMessage,
  isUploading,
  playerName,
  selectedVideoId,
  uploadMessage,
  videos,
} from '../../store';

// The sidebar is intentionally focused on source selection and lightweight
// metadata entry. It should stay free of playback and drawing logic.
@customElement('tracker-sidebar')
export class TrackerSidebar extends SignalWatcher(LitElement) {
  static styles = [
    unsafeCSS(formsStyles),
    unsafeCSS(feedbackStyles),
    unsafeCSS(sidebarStyles),
  ];

  private get videoElement(): HTMLVideoElement | null {
    return document
      .querySelector('tracker-video-panel')
      ?.shadowRoot?.querySelector('video[data-tracker-video="true"]') ?? null;
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
    return html`<aside class="tracker-sidebar">
      <section class="tracker-sidebar-card">
        <div class="tracker-sidebar-header">
          <h2 class="tracker-title">Video</h2>
        </div>
        <div class="tracker-sidebar-section">
          <label class="tracker-field">
            Stored videos
            <select
              data-testid="stored-video-select"
              class="tracker-select"
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
          <div class="tracker-divider">
            <span class="tracker-divider-text">or</span>
          </div>
          <label class="tracker-field">
            Upload new video
            <input
              data-testid="upload-video-input"
              type="file"
              accept="video/*"
              class="tracker-file-input"
              ?disabled=${isUploading.get()}
              @change=${this.handleUploadSelected}
            />
          </label>
          ${uploadMessage.get()
            ? html`<p class="tracker-message tracker-message--success">
                ${uploadMessage.get()}
              </p>`
            : null}
        </div>
      </section>

      <section class="tracker-sidebar-card tracker-sidebar-card--cyan">
        <div class="tracker-sidebar-section">
          <label class="tracker-field tracker-field--cyan">
            Player name
            <input
              class="tracker-input"
              .value=${playerName.get()}
              @input=${this.handlePlayerNameInput}
              placeholder="Enter player name here"
            />
          </label>
          ${errorMessage.get()
            ? html`<p class="tracker-message tracker-message--error">
                ${errorMessage.get()}
              </p>`
            : html``}
        </div>
      </section>
    </aside>`;
  }
}
