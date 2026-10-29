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

test('account changes clear private results before the new history arrives', async ({ page }) => {
  await mockApi(page)
  let switched = false
  let releaseHistory = () => {}
  const pendingHistory = new Promise<void>((resolve) => {
    releaseHistory = resolve
  })
  await page.route('**/api/session', (route) =>
    route.fulfill({
      json: switched
        ? { ...account, user: { id: 'separate-owner', login: 'separate-owner', name: null } }
        : account,
    }),
  )
  await page.route('**/api/runs', async (route) => {
    if (switched) await pendingHistory
    await route.fulfill({ json: { items: switched ? [] : [fixtureRun], next_cursor: null } })
  })
  await page.route('**/api/runs/fixture-run', (route) =>
    switched
      ? route.fulfill({ status: 404, json: { detail: 'Run not found.' } })
      : route.fulfill({ json: fixtureRun }),
  )
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByRole('heading', { name: fixtureRun.title })).toBeVisible()
  switched = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  try {
    await expect(page.getByText('separate-owner', { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: fixtureRun.title })).toHaveCount(0)
    await expect(page.getByRole('link', { name: fixtureRun.title })).toHaveCount(0)
  } finally {
    releaseHistory()
  }
  await expect(page.getByText('Run not found.')).toBeVisible()
})

test('account changes do not copy the previous account draft', async ({ page }) => {
  await mockApi(page)
  let switched = false
  await page.route('**/api/session', (route) =>
    route.fulfill({
      json: switched
        ? { ...account, user: { id: 'separate-owner', login: 'separate-owner', name: null } }
        : account,
    }),
  )
  await page.goto('/')
  await page
    .getByLabel('What would you like to improve?')
    .fill('Private goal belonging to the first account')
  switched = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByText('separate-owner', { exact: true }).first()).toBeVisible()
  await expect(page.getByLabel('What would you like to improve?')).toHaveValue('')
  const drafts = await page.evaluate(() => ({
    previous: sessionStorage.getItem('upgrade-run-draft:river-owner'),
    current: sessionStorage.getItem('upgrade-run-draft:separate-owner'),
  }))
  expect(drafts.previous).toContain('Private goal belonging to the first account')
  expect(drafts.current).not.toContain('Private goal belonging to the first account')
})

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

test('private snapshot fetch is explicit and shows pinned browse-only support', async ({
  page,
}, testInfo) => {
  await mockApi(page)
  await page.route('**/api/session', (route) =>
    route.fulfill({ json: { ...account, private_fetch_enabled: true } }),
  )
  const submissions: string[] = []
  await page.route('**/api/repositories**', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 41,
            full_name: 'river/project',
            owner: { login: 'river' },
            private: true,
            archived: false,
            default_branch: 'main',
            installation_id: 7,
            capability: 'browse_index',
            support_reason: 'An execution adapter is required for analysis.',
          },
        ],
        next_cursor: null,
        revision: 'snapshot-a',
        discovered_count: 1,
        sync_status: 'complete',
        updated_at: null,
      },
    }),
  )
  await page.route('**/api/repository-snapshots', async (route) => {
    const request = route.request()
    expect(request.headers()['x-csrf-token']).toBe('fixture-csrf')
    expect(request.postDataJSON()).toEqual({
      repository_id: 41,
      commit_sha: 'a'.repeat(40),
      role: 'target',
      project_root: 'packages/library',
    })
    submissions.push(request.url())
    await route.fulfill({
      status: 201,
      json: {
        id: 'pinned-fixture',
        repository_id: 41,
        full_name: 'river/project',
        installation_id: 7,
        private: true,
        commit_sha: 'a'.repeat(40),
        role: 'target',
        project_root: 'packages/library',
        capability: 'browse_index',
        file_count: 12,
        text_bytes: 4500,
        inventory_sha256: 'b'.repeat(64),
        limits_reached: [],
        created_at: 1,
      },
    })
  })
  await page.goto('/repositories')
  await page.getByText('Pin a repository snapshot', { exact: true }).click()
  await page.getByLabel('Full commit SHA').fill('a'.repeat(40))
  await page.getByLabel('Project root (optional)').fill('packages/library')
  await page.getByLabel('Repository role').selectOption('target')
  expect(submissions).toEqual([])
  await page.getByRole('button', { name: 'Fetch pinned snapshot' }).click()
  await expect(page.getByText(/Pinned 12 text files/)).toBeVisible()
  expect(submissions).toHaveLength(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/${testInfo.project.name}-f2-snapshot.png`,
    fullPage: true,
  })
})

test('installation states explain suspension and missing permissions', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/session', (route) => route.fulfill({ json: account }))
  await page.route('**/api/repositories**', (route) =>
    route.fulfill({
      json: {
        items: [],
        next_cursor: null,
        revision: null,
        discovered_count: 0,
        sync_status: 'no_installations',
        updated_at: null,
      },
    }),
  )
  await page.route('**/api/github/installations?**', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 7,
            account: { login: 'suspended-org' },
            repository_selection: 'selected',
            suspended: true,
            contents_readable: true,
          },
          {
            id: 8,
            account: { login: 'permission-org' },
            repository_selection: 'selected',
            suspended: false,
            contents_readable: false,
          },
        ],
        next_page: null,
      },
    }),
  )
  await page.goto('/repositories')
  await page.getByRole('button', { name: 'Check installations' }).click()
  await expect(page.getByText('suspended-org: Suspended')).toBeVisible()
  await expect(page.getByText('permission-org: Contents permission required')).toBeVisible()
})

test('disconnect reports when provider revocation needs manual completion', async ({ page }) => {
  await mockApi(page)
  let disconnected = false
  await page.route('**/api/session', (route) =>
    route.fulfill({
      json: {
        ...account,
        connection: disconnected ? 'reconnect_required' : 'connected',
      },
    }),
  )
  await page.route('**/api/auth/disconnect', async (route) => {
    disconnected = true
    await route.fulfill({
      json: { status: 'disconnected', provider_revocation: 'manual_revocation_required' },
    })
  })
  await page.goto('/')
  if (test.info().project.name === 'mobile')
    await page.getByRole('button', { name: 'Open navigation' }).click()
  await page.getByRole('button', { name: 'Disconnect GitHub' }).click()
  await expect(page.getByText(/GitHub could not confirm revocation/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Reconnect GitHub' })).toBeVisible()
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
