import { test, expect } from '@playwright/test'
import { mockApi, fixtureRun } from './fixtures'

const account = {
  mode: 'github',
  authenticated: true,
  user: { id: 'river-owner', login: 'river-builds', name: null },
  csrf_token: 'fixture-csrf',
  connection: 'connected',
  install_url: 'https://github.com/apps/upgrade-fixture/installations/new',
}

test('signed-out accounts see onboarding without loading private runs', async ({
  page,
}, testInfo) => {
  await mockApi(page)
  await page.route('**/api/session', (route) =>
    route.fulfill({
      json: {
        ...account,
        authenticated: false,
        user: null,
        csrf_token: null,
        connection: 'signed_out',
      },
    }),
  )
  const reads: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/runs')) reads.push(request.url())
  })
  await page.goto('/runs/private-id/overview')
  await expect(page.getByRole('heading', { name: 'Your projects, connected' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Sign in with GitHub' })).toHaveAttribute(
    'href',
    '/api/auth/github/start?return_to=%2Fruns%2Fprivate-id%2Foverview',
  )
  expect(reads).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/${testInfo.project.name}-sign-in.png`,
    fullPage: true,
  })
})

test('repository sync sends CSRF and browsing does not run a model', async ({ page }, testInfo) => {
  await mockApi(page)
  await page.route('**/api/session', (route) => route.fulfill({ json: account }))
  const submissions: string[] = []
  let synced = false
  await page.route('**/api/repositories**', async (route) => {
    const request = route.request()
    if (request.method() === 'POST') {
      expect(request.headers()['x-csrf-token']).toBe('fixture-csrf')
      submissions.push(request.url())
      synced = true
    }
    const query = new URL(request.url()).searchParams.get('q') ?? ''
    await route.fulfill({
      json: {
        items:
          synced && (!query || query.includes('httpx'))
            ? [
                {
                  id: 1,
                  full_name: 'encode/httpx',
                  owner: { login: 'encode' },
                  private: false,
                  archived: false,
                  default_branch: 'master',
                  installation_id: 7,
                  capability: 'browse_index',
                  support_reason: 'An execution adapter is required for analysis.',
                },
              ]
            : [],
        next_cursor: null,
        revision: synced ? 'snapshot-a' : null,
        discovered_count: synced ? 1 : 0,
        sync_status: synced ? 'complete' : 'not_synced',
        updated_at: null,
      },
    })
  })
  await page.goto('/repositories')
  await expect(page.getByRole('heading', { name: 'Connect your project list' })).toBeVisible()
  await page.getByRole('button', { name: 'Sync repositories' }).click()
  await expect(page.getByRole('link', { name: 'encode/httpx' })).toBeVisible()
  await page.getByLabel('Search repositories').fill('missing-project')
  await expect(page.getByRole('heading', { name: 'No matching repositories' })).toBeVisible()
  await page.getByLabel('Search repositories').fill('httpx')
  await expect(page.getByRole('link', { name: 'encode/httpx' })).toBeVisible()
  expect(submissions).toHaveLength(1)
  expect(submissions[0]).toContain('/api/repositories/sync')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/${testInfo.project.name}-repositories.png`,
    fullPage: true,
  })
})

test('history search queries the server and keeps URL context', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/runs?**', async (route) => {
    const query = new URL(route.request().url()).searchParams.get('q') ?? ''
    await route.fulfill({
      json: {
        items:
          query === 'older project'
            ? [{ ...fixtureRun, id: 'beyond-first-page', title: 'Older project result' }]
            : [],
        next_cursor: null,
      },
    })
  })
  await page.goto('/runs')
  await page.getByLabel('Search runs').fill('older project')
  await expect(page.getByRole('link', { name: /Older project result/ }).first()).toBeVisible()
  await expect(page).toHaveURL(/q=older\+project/)
  await page.reload()
  await expect(page.getByLabel('Search runs')).toHaveValue('older project')
  await expect(page.getByRole('link', { name: /Older project result/ }).first()).toBeVisible()
})

test('event catch-up stays bounded to the latest 200 entries', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/runs/fixture-run/events?**', async (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get('after'))
    const items =
      after < 300
        ? Array.from({ length: 100 }, (_, index) => ({
            sequence: after + index + 1,
            run_id: 'fixture-run',
            timestamp: '2026-10-28T12:00:00Z',
            stage: 'analysis',
            message: `event-${after + index + 1}`,
          }))
        : []
    await route.fulfill({ json: { items, next_cursor: after + items.length } })
  })
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByText('200 recent events (up to 200 shown)')).toBeVisible()
  await expect(page.getByText('event-300', { exact: true }).first()).toBeAttached()
  await expect(page.getByText('event-1', { exact: true })).toHaveCount(0)
})
