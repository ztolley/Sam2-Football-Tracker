import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(currentDir, '../..');
const fixtureDir = path.join(repoRoot, 'output', 'playwright', 'fixtures');
const backendDir = path.join(repoRoot, 'output', 'playwright', 'backend');
const videoPath = path.join(fixtureDir, 'moving-dot.mp4');
const manifestPath = path.join(fixtureDir, 'moving-dot.json');

function generateFixture(): void {
  fs.mkdirSync(fixtureDir, { recursive: true });
  execFileSync(
    path.join(repoRoot, 'server', '.venv', 'bin', 'python'),
    [
      path.join(repoRoot, 'scripts', 'generate_moving_dot_video.py'),
      videoPath,
      '--manifest',
      manifestPath,
      '--width',
      '854',
      '--height',
      '480',
      '--frames',
      '24',
    ],
    { stdio: 'inherit' },
  );
}

test.beforeAll(() => {
  generateFixture();
});

test.afterAll(() => {
  fs.rmSync(fixtureDir, { recursive: true, force: true });
  fs.rmSync(backendDir, { recursive: true, force: true });
});

test('uploads, selects, processes, plays, and renders the output movie', async ({
  page,
}) => {
  await page.goto('/');

  await page.getByTestId('upload-video-input').setInputFiles(videoPath);

  await expect(page.getByText('Uploaded moving-dot.mp4')).toBeVisible();
  await expect
    .poll(
      async () =>
        page
          .getByTestId('tracker-video')
          .evaluate((video: HTMLVideoElement) => video.currentSrc),
      { timeout: 15_000 },
    )
    .toContain('/media/uploads/');
  await expect
    .poll(
      async () =>
        page
          .getByTestId('tracker-video')
          .evaluate((video: HTMLVideoElement) => video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA),
      { timeout: 15_000 },
    )
    .toBeTruthy();

  await page.reload({ waitUntil: 'networkidle' });
  await page.getByTestId('stored-video-select').selectOption({ index: 1 });
  await expect
    .poll(
      async () =>
        page
          .getByTestId('tracker-video')
          .evaluate((video: HTMLVideoElement) => video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA),
      { timeout: 15_000 },
    )
    .toBeTruthy();

  await page.getByTestId('tracker-video').evaluate((video: HTMLVideoElement) => {
    video.pause();
    video.currentTime = 0;
  });

  await page.getByTestId('draw-player-button').click();
  const drawSurface = page.getByTestId('draw-surface');
  const bounds = await drawSurface.boundingBox();
  if (!bounds) {
    throw new Error('Draw surface was not visible');
  }

  await page.mouse.move(bounds.x + 20, bounds.y + bounds.height / 2 - 24);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 72, bounds.y + bounds.height / 2 + 24, {
    steps: 8,
  });
  await page.mouse.up();

  await page.getByTestId('confirm-selection-button').click();
  await expect(page.getByTestId('processing-overlay')).toBeVisible();
  await expect(page.getByTestId('processing-overlay')).toBeHidden({
    timeout: 30_000,
  });

  await expect
    .poll(
      async () =>
        page
          .getByTestId('tracker-video')
          .evaluate((video: HTMLVideoElement) => video.currentSrc),
      { timeout: 30_000 },
    )
    .toContain('/media/jobs/');
  await expect
    .poll(
      async () =>
        page
          .getByTestId('tracker-video')
          .evaluate((video: HTMLVideoElement) => video.currentSrc),
      { timeout: 30_000 },
    )
    .toContain('processed.mp4');

  await page.getByTestId('tracker-video').evaluate(async (video: HTMLVideoElement) => {
    await video.play();
  });
  const playbackStart = await page
    .getByTestId('tracker-video')
    .evaluate((video: HTMLVideoElement) => video.currentTime);
  await page.waitForTimeout(1000);
  const playbackEnd = await page
    .getByTestId('tracker-video')
    .evaluate((video: HTMLVideoElement) => video.currentTime);
  expect(playbackEnd).toBeGreaterThan(playbackStart);

  await page.getByTestId('render-movie-button').click();
  await expect(page.getByTestId('processing-overlay')).toBeVisible();
  await expect(page.getByTestId('processing-overlay')).toBeHidden({
    timeout: 30_000,
  });

  const renderedLink = page.getByTestId('rendered-movie-link');
  await expect(renderedLink).toBeVisible();
  const href = await renderedLink.getAttribute('href');
  expect(href).toBeTruthy();
  const renderedUrl = new URL(href!, 'http://127.0.0.1:4174');
  const httpResponse = await page.request.get(renderedUrl.toString());
  expect(httpResponse.ok()).toBeTruthy();

  const [, , , jobId, filename] = renderedUrl.pathname.split('/');
  const renderedPath = path.join(
    repoRoot,
    'output',
    'playwright',
    'backend',
    'jobs',
    jobId,
    filename,
  );
  expect(fs.existsSync(renderedPath)).toBeTruthy();
});
