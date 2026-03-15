import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html, unsafeCSS } from 'lit';
import { customElement } from 'lit/decorators.js';

import layoutStyles from '../../../styles/layout.css?inline';
import '../tracker-sidebar/tracker-sidebar';
import '../tracker-video-panel/tracker-video-panel';
import appStyles from './tracker-app.css?inline';
import { loadInitialTrackerState } from '../../actions';

// The app shell stays intentionally thin: it boots shared tracker state,
// renders the top-level layout, and leaves feature behavior to child components.
@customElement('tracker-app')
export class TrackerApp extends SignalWatcher(LitElement) {
  static styles = [unsafeCSS(layoutStyles), unsafeCSS(appStyles)];

  connectedCallback(): void {
    super.connectedCallback();
    // Bootstrap data once the shell is attached so child components can read
    // from the shared store immediately.
    void loadInitialTrackerState(() => this.videoElement);
  }

  private get videoElement(): HTMLVideoElement | null {
    return this.renderRoot
      .querySelector('tracker-video-panel')
      ?.shadowRoot?.querySelector('video[data-tracker-video="true"]') ?? null;
  }

  render() {
    return html`
      <main class="tracker-page">
        <section class="tracker-shell">
          <header class="tracker-hero">
            <div class="tracker-hero-content">
              <div class="tracker-hero-copy">
                <h1 class="tracker-hero-title">Football Tracker</h1>
                <p class="tracker-hero-text">
                  Select a video witgh a football player in it, highlight them,
                  and track that player throughout the video.
                </p>
              </div>
            </div>
          </header>

          <section class="tracker-grid">
            <tracker-video-panel></tracker-video-panel>
            <tracker-sidebar></tracker-sidebar>
          </section>
        </section>
      </main>
    `;
  }
}
