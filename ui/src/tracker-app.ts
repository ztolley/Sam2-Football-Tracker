import { SignalWatcher } from '@lit-labs/signals';
import { LitElement, html } from 'lit';
import { customElement } from 'lit/decorators.js';

import './features/tracker/components/tracker-sidebar';
import './features/tracker/components/tracker-video-panel';
import { loadInitialTrackerState } from './features/tracker/actions';
import { apiStatus } from './features/tracker/store';

// The app shell stays intentionally thin: it boots shared tracker state,
// renders the top-level layout, and leaves feature behavior to child components.
@customElement('tracker-app')
export class TrackerApp extends SignalWatcher(LitElement) {
  protected createRenderRoot(): this {
    return this;
  }

  connectedCallback(): void {
    super.connectedCallback();
    // Bootstrap data once the shell is attached so child components can read
    // from the shared store immediately.
    void loadInitialTrackerState(() => this.videoElement);
  }

  private get videoElement(): HTMLVideoElement | null {
    return this.querySelector('video[data-tracker-video="true"]');
  }

  render() {
    return html`
      <main
        class="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(255,0,146,0.24),_transparent_26%),radial-gradient(circle_at_top_right,_rgba(0,240,255,0.18),_transparent_32%),radial-gradient(circle_at_bottom_center,_rgba(190,242,100,0.14),_transparent_26%),linear-gradient(180deg,_#180021_0%,_#0a0515_50%,_#06070c_100%)] px-4 py-6 text-fuchsia-50 sm:px-6 lg:px-8"
      >
        <section class="mx-auto flex w-full max-w-7xl flex-col gap-6">
          <header
            class="overflow-hidden rounded-[28px] border border-fuchsia-500/30 bg-[#12071f]/80 px-6 py-6 shadow-[0_0_0_1px_rgba(244,114,182,0.14),0_24px_80px_rgba(0,0,0,0.42)] backdrop-blur"
          >
            <div
              class="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
            >
              <div class="max-w-3xl">
                <h1
                  class="mt-3 max-w-2xl bg-[linear-gradient(90deg,_#ff5dc8_0%,_#8cf7ff_55%,_#d9ff66_100%)] bg-clip-text font-['Space_Grotesk'] text-4xl font-semibold tracking-tight text-transparent sm:text-5xl"
                >
                  Football Tracker
                </h1>
                <p
                  class="mt-3 max-w-2xl text-sm leading-6 text-fuchsia-50/78 sm:text-base"
                >
                  Select a video witgh a football player in it, highlight them,
                  and track that player throughout the video.
                </p>
              </div>
              <div
                class="rounded-full border border-cyan-300/25 bg-[#09111c]/80 px-4 py-2 font-mono text-xs uppercase tracking-[0.28em] text-cyan-100 shadow-[0_0_18px_rgba(34,211,238,0.12)]"
              >
                API ${apiStatus.get()}
              </div>
            </div>
          </header>

          <section
            class="grid gap-6 xl:grid-cols-[minmax(0,1.9fr)_minmax(300px,0.8fr)]"
          >
            <tracker-video-panel></tracker-video-panel>
            <tracker-sidebar></tracker-sidebar>
          </section>
        </section>
      </main>
    `;
  }
}
