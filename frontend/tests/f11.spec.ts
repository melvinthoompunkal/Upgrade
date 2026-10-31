import { test, expect } from '@playwright/test'
import { mockApi, fixtureRun } from './fixtures'

test('branch selection pins one immutable SHA and editing the branch invalidates it', async ({
  page,
}, info) => {
  await mockApi(page)
  let resolutions = 0
  let pin: Record<string, unknown> | null = null
  await page.route('**/api/projects/resolve-ref', (route) => {
    resolutions++
    expect(route.request().postDataJSON()).toEqual({
      url: 'https://github.com/river/async-tools',
      kind: 'branch',
      name: 'release/v2',
    })
    return route.fulfill({
      json: { kind: 'branch', name: 'release/v2', commit_sha: 'b'.repeat(40), repository_id: null },
    })
  })
  await page.route('**/api/projects', (route) => {
    pin = route.request().postDataJSON()
    return route.fulfill({
      json: {
        id: 'a'.repeat(32),
        requested: pin,
        snapshot: { ...fixtureRun.source, commit_sha: 'b'.repeat(40) },
        support: {
          name: 'async-tools',
          python_files: 3,
          reasons: [],
          capability: 'evidence_analysis',
          execution_supported: true,
          adapter_version: 'python-pytest-asyncio-v1',
          adaptation_reason: 'Feature-specific review required.',
        },
        root_choices: [''],
      },
    })
  })
  await page.goto('/')
  await page.getByLabel('Source public repository').fill('https://github.com/river/async-tools')
  await page.getByLabel('Source revision type').selectOption('branch')
  await page.getByLabel('Source branch', { exact: true }).fill('release/v2')
  await page.getByRole('button', { name: 'Resolve branch', exact: true }).click()
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('b'.repeat(40))
  await expect(page.getByLabel('Source commit SHA')).toHaveAttribute('readonly', '')
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeEnabled()
  expect(pin).toMatchObject({ commit_sha: 'b'.repeat(40), role: 'source' })
  expect(pin).not.toHaveProperty('branch')
  expect(resolutions).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/f11-${info.project.name}-revision.png`,
    fullPage: true,
  })
  await page.getByLabel('Source branch', { exact: true }).fill('another-branch')
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeDisabled()
})

test('late ref resolution cannot populate another repository', async ({ page }) => {
  await mockApi(page)
  let release = () => {}
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/projects/resolve-ref', async (route) => {
    await pending
    await route.fulfill({ json: { commit_sha: 'c'.repeat(40) } })
  })
  await page.goto('/')
  await page.getByLabel('Source public repository').fill('https://github.com/river/first')
  await page.getByLabel('Source revision type').selectOption('tag')
  await page.getByLabel('Source tag', { exact: true }).fill('v2')
  await page.getByRole('button', { name: 'Resolve tag', exact: true }).click()
  await expect(page.getByText('Resolving tag to a commit…')).toBeVisible()
  await page.getByLabel('Source public repository').fill('https://github.com/river/second')
  release()
  await expect(page.getByRole('button', { name: 'Resolve tag', exact: true })).toBeEnabled()
  await expect(page.getByLabel('Source commit SHA')).toHaveValue('')
})

test('support matrix is explicit and keeps private analysis disabled', async ({ page }) => {
  await mockApi(page)
  let calls = 0
  await page.route('**/api/projects/support-matrix', (route) => {
    calls++
    return route.fulfill({
      json: {
        runtime_profile: 'python312-http-v2',
        runtime_packages: { httpx: '0.28.1', anyio: '4.15.1' },
        native_adapters: ['python-pytest-asyncio-v1'],
        acceptance_adapters: ['python-integration-acceptance-v2'],
        source_use_profiles: { MIT: 'standard-mit-v1', 'BSD-2-Clause': 'standard-bsd-2-clause-v1' },
        private_preflight_enabled: false,
        private_analysis_enabled: false,
        unsupported: ['Private analysis'],
      },
    })
  })
  await page.goto('/system')
  expect(calls).toBe(0)
  await page.getByRole('button', { name: 'Review supported profiles' }).click()
  await expect(page.getByText(/python312-http-v2/)).toBeVisible()
  await expect(page.getByText(/Analysis disabled pending live validation/)).toBeVisible()
  expect(calls).toBe(1)
})
