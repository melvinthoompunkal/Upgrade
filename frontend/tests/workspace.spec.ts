import { test, expect } from '@playwright/test'
import { mockApi, fixtureRun, capabilities } from './fixtures'

test('example pins source and editing the repository clears its SHA', async ({ page }) => {
  await mockApi(page)
  await page.goto('/example')
  await page.getByRole('button', { name: 'Use Scrapy example' }).click()
  await expect(page.getByLabel('Source repository')).toHaveValue('https://github.com/scrapy/scrapy')
  await page.getByRole('button', { name: /Analysis options/ }).click()
  await expect(page.getByLabel('Source commit SHA')).toHaveValue(
    '5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac',
  )
  await page.getByLabel('Source repository').fill('https://github.com/example/another')
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('')
  await expect(page.getByText('Latest source snapshot', { exact: true })).toBeVisible()
  await expect(page.getByLabel('What would you like to improve?')).not.toHaveValue('')
})

test('blank source SHA starts a trace using the latest snapshot', async ({ page }) => {
  await mockApi(page)
  await page.goto('/example')
  await page.getByLabel('Source repository').fill('https://github.com/scrapy/scrapy')
  await page.getByLabel('What would you like to improve?').fill('Understand the retry behavior.')
  await page.getByRole('button', { name: /Analysis options/ }).click()
  await page.getByLabel('Source commit SHA').clear()
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('')
  await expect(page.getByText(/latest default-branch commit when the run starts/)).toBeVisible()
  await page.getByLabel('Trace source only').check()
  const submission = page.waitForRequest(
    (request) => request.method() === 'POST' && new URL(request.url()).pathname === '/api/runs',
  )
  await page.getByRole('button', { name: 'Trace source', exact: true }).click()
  const body = (await submission).postDataJSON()
  expect(body).toMatchObject({
    kind: 'source_trace',
    source_url: 'https://github.com/scrapy/scrapy',
  })
  expect(body).not.toHaveProperty('source_sha')
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
})

test('invalid source SHA blocks submission and retains the form', async ({ page }) => {
  await mockApi(page)
  const submissions: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST') submissions.push(request.url())
  })
  await page.goto('/example')
  await page.getByLabel('Source repository').fill('https://github.com/scrapy/scrapy')
  await page.getByLabel('What would you like to improve?').fill('Understand retries.')
  await page.getByRole('button', { name: /Analysis options/ }).click()
  await page.getByLabel('Source commit SHA').fill('main')
  await page.getByLabel('Trace source only').check()
  await page.getByRole('button', { name: 'Trace source', exact: true }).click()
  await expect(
    page.getByText('Enter the full commit SHA, or leave it blank to use the latest snapshot.'),
  ).toBeVisible()
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('main')
  expect(submissions).toHaveLength(0)
})

test('invalid URL preserves the form and never queues work', async ({ page }) => {
  await mockApi(page)
  let submissions = 0
  page.on('request', (request) => {
    if (request.method() === 'POST') submissions++
  })
  await page.goto('/example')
  await page.getByLabel('Source repository').fill('https://secret@github.com/owner/repo')
  await page.getByLabel('What would you like to improve?').fill('Preserve this goal.')
  await page.getByRole('button', { name: 'Find a suitable feature', exact: true }).click()
  await expect(page.getByText(/without credentials or query parameters/)).toBeVisible()
  await expect(page.getByLabel('What would you like to improve?')).toHaveValue(
    'Preserve this goal.',
  )
  expect(submissions).toBe(0)
})

for (const mode of ['fit_select', 'source_trace']) {
  test(`crawler example blocks OpenClaw before submission in ${mode}`, async ({ page }) => {
    await mockApi(page)
    let submissions = 0
    page.on('request', (request) => {
      if (request.method() === 'POST') submissions++
    })
    await page.goto('/example')
    await expect(page.getByLabel('Source repository')).toHaveValue(
      'https://github.com/scrapy/scrapy',
    )
    await page.getByRole('button', { name: /Analysis options/ }).click()
    await expect(page.getByLabel('Source commit SHA')).toHaveValue(
      '5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac',
    )
    await expect(page.getByText(/Node\/TypeScript projects such as OpenClaw/)).toBeVisible()
    await expect(
      page.getByRole('link', { name: 'Feature discovery', exact: true }),
    ).toHaveAttribute('href', '/')
    await page.getByLabel('Source repository').fill('https://github.com/openclaw/openclaw')
    await page
      .getByLabel('What would you like to improve?')
      .fill('Increase web scraping capabilities.')
    if (mode === 'source_trace') {
      await page.getByLabel('Trace source only').check()
    }
    await page
      .getByRole('button', {
        name: mode === 'source_trace' ? 'Trace source' : 'Find a suitable feature',
        exact: true,
      })
      .click()
    await expect(page.getByText(/The crawler example supports only Scrapy\./)).toBeVisible()
    await expect(page.getByLabel('Source repository')).toBeFocused()
    await expect(page).toHaveURL(/\/example$/)
    expect(submissions).toBe(0)
  })
}

test('run creation is idempotent and opens a stable workspace URL', async ({ page }) => {
  await mockApi(page)
  const submissions: string[] = []
  const sourceShas: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST') {
      submissions.push(request.headers()['idempotency-key'])
      sourceShas.push(request.postDataJSON().source_sha)
    }
  })
  await page.goto('/example')
  await page.getByRole('button', { name: 'Use Scrapy example' }).click()
  await page.getByRole('button', { name: 'Find a suitable feature', exact: true }).dblclick()
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
  await expect(page.getByRole('heading', { name: fixtureRun.title })).toBeVisible()
  expect(submissions).toHaveLength(1)
  expect(submissions[0]).toMatch(/^[a-f0-9-]{36}$/)
  expect(sourceShas).toEqual(['5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac'])
  await page.reload()
  await expect(page.getByRole('heading', { name: fixtureRun.title })).toBeVisible()
  expect(submissions).toHaveLength(1)
})

test('features use actual candidate counts and controller scores', async ({ page }) => {
  await mockApi(page)
  await page.goto('/runs/fixture-run/features')
  await expect(
    page.getByText('1 analyzed / 1 eligible. Only actual candidates are shown.'),
  ).toBeVisible()
  await expect(page.locator('.feature-table tbody tr')).toHaveCount(1)
  await expect(page.getByRole('cell', { name: '83 / 100' })).toBeVisible()
  await page.getByLabel('Filter feature decisions').selectOption('rejected')
  await expect(page.getByRole('heading', { name: 'No matching candidates' })).toBeVisible()
  await expect(
    page.getByText('1 analyzed / 1 eligible. Only actual candidates are shown.'),
  ).toBeVisible()
})

test('citation inspector opens an exact excerpt and restores focus', async ({ page }) => {
  await mockApi(page)
  await page.goto('/runs/fixture-run/evidence')
  const origin = page.getByRole('button', { name: 'Inspect retry.py lines 1 to 3' })
  await origin.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('def retry(response):', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/ref=test-reference/)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(origin).toBeFocused()
})

test('expected retry diagnostic stays distinct from the ordinary baseline', async ({ page }) => {
  await mockApi(page)
  await page.goto('/runs/fixture-run/tests')
  await expect(page.getByText('Missing retry behavior was observed.')).toBeVisible()
  await expect(page.getByText('All registered cases passed', { exact: true })).toBeVisible()
  await page.getByLabel('Filter test results').selectOption('failed')
  await expect(page.getByText('test_fetch', { exact: true })).toHaveCount(0)
  await page.getByText('test_transient_failure', { exact: true }).click()
  await expect(page.getByText('Expected three attempts, observed one.')).toBeVisible()
})

test('exports preview and download only registered artifacts', async ({ page }) => {
  await mockApi(page)
  await page.goto('/runs/fixture-run/exports')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('{"status":"selected"}')
  await expect(page.getByRole('link', { name: 'Download artifact' })).toHaveAttribute(
    'href',
    '/api/runs/fixture-run/artifacts/test-report',
  )
  await expect(page.getByRole('link', { name: 'Download artifact' })).toHaveAttribute(
    'download',
    'report.json',
  )
})

test('no-fit results retain the candidate and explain every rejection', async ({ page }) => {
  const run = structuredClone(fixtureRun)
  run.outcome = 'no_safe_fit'
  run.eligible_feature_count = 0
  run.fit!.selected_feature_id = null
  run.fit!.top3 = []
  run.fit!.candidates[0].eligible = false
  run.fit!.candidates[0].gates[0].passed = false
  run.fit!.selection_reason = 'No candidate passes every required gate.'
  run.fit!.rejected = [{ feature_id: 'retry', reasons: ['The target already has retry behavior.'] }]
  await mockApi(page, run)
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByText('No suitable fit', { exact: true })).toBeVisible()
  await expect(page.getByText('The target already has retry behavior.')).toBeVisible()
  await expect(page.getByText('Suitable feature found', { exact: true })).toHaveCount(0)
  await page.goto('/runs/fixture-run/features')
  await expect(page.locator('.feature-table tbody tr')).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'Not eligible' })).toBeVisible()
})

test('active run reconnects and catches up without repeating execution', async ({ page }) => {
  await mockApi(page)
  let reads = 0
  const submissions: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST') submissions.push(request.url())
  })
  await page.route('**/api/runs/fixture-run', async (route) => {
    reads++
    if (reads === 2) return route.abort('failed')
    await route.fulfill({
      json:
        reads === 1
          ? { ...fixtureRun, status: 'running', stage: 'analysis', outcome: null }
          : fixtureRun,
    })
  })
  await page.goto('/runs/fixture-run/overview')
  await expect(
    page.getByText(
      'Connection lost. Reconnecting to this run; its execution continues on the server.',
    ),
  ).toBeVisible()
  await expect(page.getByText('Suitable feature found', { exact: true })).toBeVisible()
  await expect(page.getByText(/Connection lost/)).toHaveCount(0)
  expect(submissions).toHaveLength(0)
})

test('zero-feature failure and analysis-only changes are explicit', async ({ page }) => {
  const run = {
    ...fixtureRun,
    outcome: 'failed',
    analyzed_feature_count: 0,
    eligible_feature_count: 0,
    features: [],
    fit: null,
    references: [],
    error: { code: 'budget_exhausted', message: 'The analysis reached its allowance.' },
  }
  await mockApi(page, run)
  await page.goto('/runs/fixture-run/features')
  await expect(
    page.getByRole('heading', { name: 'No source features were analyzed' }),
  ).toBeVisible()
  await page.goto('/runs/fixture-run/changes')
  await expect(page.getByRole('heading', { name: 'This run made no code changes.' })).toBeVisible()
  await expect(page.getByRole('button', { name: /implement|build patch/i })).toHaveCount(0)
})

test('missing configuration leaves historical runs accessible', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/capabilities', (route) =>
    route.fulfill({ json: { ...capabilities, model_configured: false } }),
  )
  await page.goto('/example')
  await expect(
    page.getByRole('button', { name: 'Find a suitable feature', exact: true }),
  ).toBeDisabled()
  await expect(page.getByRole('link', { name: /Review setup in System checks/ })).toBeVisible()
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByRole('heading', { name: fixtureRun.title })).toBeVisible()
})

test('all run views fit the viewport and support mobile navigation', async ({ page }, testInfo) => {
  await mockApi(page)
  for (const path of [
    '/',
    '/runs',
    '/system',
    ...['overview', 'features', 'evidence', 'tests', 'changes', 'exports'].map(
      (view) => `/runs/fixture-run/${view}`,
    ),
  ]) {
    await page.goto(path)
    await expect(page.locator('main h1')).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      path,
    ).toBe(true)
  }
  if (testInfo.project.name === 'mobile') {
    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page
      .getByRole('complementary', { name: 'Main navigation' })
      .getByRole('link', { name: 'New run', exact: true })
      .click()
    await expect(page).toHaveURL('/')
    await expect(page.getByRole('button', { name: 'Open navigation' })).toBeVisible()
  }
})
