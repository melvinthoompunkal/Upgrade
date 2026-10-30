import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

// Invoked by scripts/validate_release.py after building the production bundle.
const root = process.env.GRAFT_RELEASE_ROOT
const port = Number(process.env.GRAFT_RELEASE_PORT)
const python = process.env.GRAFT_RELEASE_PYTHON
if (!root || !python || !Number.isInteger(port) || port < 1 || port > 65535)
  throw new Error('Run scripts/validate_release.py to supply an isolated release environment.')

export default defineConfig({
  testDir: './tests',
  testIgnore: [
    '**/projects-live.spec.ts',
    '**/live-readonly.spec.ts',
    '**/change-review-recorded.spec.ts',
    ...(process.env.GRAFT_RELEASE_DOCKER === '1' ? [] : ['**/release-review.spec.ts']),
  ],
  outputDir: resolve(root, 'browser-artifacts'),
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['junit', { outputFile: resolve(root, 'browser-junit.xml') }],
    ['json', { outputFile: resolve(root, 'browser-results.json') }],
  ],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1100 } } },
    { name: 'tablet', use: { viewport: { width: 768, height: 1100 } } },
    {
      name: 'mobile',
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
  webServer: {
    command: `"${python}" -m scripts.release_server --root "${resolve(root, 'server')}" --port ${port}`,
    cwd: resolve(import.meta.dirname, '..'),
    url: `http://127.0.0.1:${port}/health`,
    timeout: 60_000,
    reuseExistingServer: false,
  },
})
