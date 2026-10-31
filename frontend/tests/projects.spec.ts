import { test, expect } from '@playwright/test'
import { mockApi, fixtureRun } from './fixtures'
import type { ProjectSelection } from '../src/api/client'

function project(role: 'source' | 'target', supported = true): ProjectSelection {
  return {
    inventory_policy: 'python-inventory-v1',
    private: false,
    id: (role === 'source' ? 'a' : 'b').repeat(32),
    requested: {
      analysis_profile: 'python-evidence-v1',
      url: 'https://github.com/team/sequence-tools',
      commit_sha: 'c'.repeat(40),
      role,
      project_root: 'library',
    },
    snapshot: { ...fixtureRun.source!, repo_id: role, commit_sha: 'c'.repeat(40) },
    support: {
      runtime_profile: 'python312-http-v2',
      profile_version: 'python-evidence-v1',
      repo_id: role,
      commit_sha: 'c'.repeat(40),
      project_root: 'library',
      python_files: 3,
      manifest_sha256: 'd'.repeat(64),
      capability: supported ? 'evidence_analysis' : 'browse_index',
      execution_supported: supported,
      adaptation_supported: false,
      notice_policy: 'analysis-only-v1',
      reasons: supported ? [] : ['No Python project manifest at the selected root.'],
      name: 'sequence-tools',
      adaptation_reason: 'Feature-specific acceptance and allowed-use review are required.',
    },
    inventory_sha256: 'e'.repeat(64),
    root_choices: ['library'],
    created_at: '2026-10-28T21:00:00Z',
  }
}

async function fillPin(page: import('@playwright/test').Page, role: 'Source' | 'Target') {
  await page.getByLabel(`${role} public repository`).fill('https://github.com/team/sequence-tools')
  await page.getByLabel(`${role} revision type`).selectOption('commit')
  await page.getByLabel(`${role} commit SHA`).fill('c'.repeat(40))
  await page.getByLabel(`${role} project root`, { exact: true }).fill('library')
  await page.getByRole('button', { name: `Inspect ${role.toLowerCase()} project` }).click()
}

for (const revision of ['latest', 'commit']) {
  test(`source inspection accepts a URL without a SHA in ${revision} mode`, async ({ page }) => {
    await mockApi(page)
    let submitted: Record<string, unknown> | undefined
    await page.route('**/api/projects', (route) => {
      submitted = route.request().postDataJSON()
      return route.fulfill({ json: project('source') })
    })
    await page.goto('/')
    await expect(page.getByLabel('Source revision type')).toHaveValue('latest')
    await expect(page.getByLabel('Source commit SHA')).toHaveCount(0)
    await page.getByLabel('Source public repository').fill('https://github.com/team/sequence-tools')
    await page.getByLabel('Source revision type').selectOption(revision)
    if (revision === 'commit') await expect(page.getByLabel('Source commit SHA')).toHaveValue('')
    await page.getByRole('button', { name: 'Inspect source project' }).click()
    await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeEnabled()
    await expect(page.getByText('Snapshot cccccccc', { exact: true })).toBeVisible()
    expect(submitted).toEqual({
      url: 'https://github.com/team/sequence-tools',
      project_root: '',
      role: 'source',
    })
    await page.getByLabel('Source project root', { exact: true }).fill('another-root')
    await expect(
      page.getByRole('button', { name: 'Discover features', exact: true }),
    ).toBeDisabled()
  })
}

test('unresolved named refs and invalid explicit SHAs cannot fall back to latest', async ({
  page,
}) => {
  await mockApi(page)
  const inspections: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/projects') inspections.push(request.url())
  })
  await page.goto('/')
  await page.getByLabel('Source public repository').fill('https://github.com/team/sequence-tools')
  await page.getByLabel('Source revision type').selectOption('branch')
  await page.getByLabel('Source branch', { exact: true }).fill('release')
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await expect(
    page.getByText('Resolve the selected branch before inspecting the project.'),
  ).toBeVisible()
  await page.getByLabel('Source revision type').selectOption('commit')
  await page.getByLabel('Source commit SHA').fill('main')
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await expect(
    page.getByText(/Enter the full lowercase commit SHA, or leave it blank/),
  ).toBeVisible()
  expect(inspections).toHaveLength(0)
})

test('late latest-snapshot inspection cannot enable discovery for a changed URL', async ({
  page,
}) => {
  await mockApi(page)
  let release = () => {}
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/projects', async (route) => {
    await pending
    await route.fulfill({ json: project('source') })
  })
  await page.goto('/')
  await page.getByLabel('Source public repository').fill('https://github.com/team/sequence-tools')
  const inspection = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/projects',
  )
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await inspection
  await page.getByLabel('Source public repository').fill('https://github.com/team/another')
  release()
  await expect(page.getByRole('button', { name: 'Inspect source project' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeDisabled()
  await expect(page.getByText('Snapshot cccccccc', { exact: true })).toHaveCount(0)
})

test('optional target inspection also defaults to its latest snapshot', async ({ page }) => {
  await mockApi(page)
  const submitted: Record<string, unknown>[] = []
  await page.route('**/api/projects', (route) => {
    const body = route.request().postDataJSON()
    submitted.push(body)
    return route.fulfill({ json: project(body.role) })
  })
  await page.goto('/?view=structure')
  for (const role of ['Source', 'Target']) {
    if (role === 'Target') await page.getByLabel('Add a target project').check()
    await expect(page.getByLabel(`${role} revision type`)).toHaveValue('latest')
    await page
      .getByLabel(`${role} public repository`)
      .fill('https://github.com/team/sequence-tools')
    await page.getByRole('button', { name: `Inspect ${role.toLowerCase()} project` }).click()
    await expect(
      page.getByRole('button', { name: `Inspect ${role.toLowerCase()} project` }),
    ).toBeEnabled()
  }
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeEnabled()
  expect(submitted.map((body) => body.role)).toEqual(['source', 'target'])
  expect(submitted.every((body) => !('commit_sha' in body))).toBe(true)
})

test('discovery is the default and structure inspection works without a model key', async ({
  page,
}, testInfo) => {
  await mockApi(page)
  await page.route('**/api/capabilities', (route) =>
    route.fulfill({ json: { model_configured: false, worker_available: true } }),
  )
  await page.route('**/api/projects', (route) => route.fulfill({ json: project('source') }))
  let submitted: Record<string, unknown> | undefined
  await page.route('**/api/runs', (route) => {
    if (route.request().method() === 'POST') submitted = route.request().postDataJSON()
    return route.fulfill({
      json:
        route.request().method() === 'POST'
          ? { ...fixtureRun, kind: 'project_analysis' }
          : { items: [] },
    })
  })
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: 'Break a repository into features.' }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Feature discovery', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Add a target project')).toHaveCount(0)
  await page.getByRole('button', { name: 'Structure only', exact: true }).click()
  await expect(page.getByLabel('Add a target project')).not.toBeChecked()
  await expect(page.getByRole('link', { name: 'Try the crawler example' })).toHaveAttribute(
    'href',
    '/example',
  )
  await fillPin(page, 'Source')
  await expect(page.getByText('Ready for feature discovery', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({
    path: `../data/frontend-qa/f3-${testInfo.project.name}-projects.png`,
    fullPage: true,
  })
  await page.getByRole('button', { name: 'Analyze projects' }).click()
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
  expect(submitted).toMatchObject({
    kind: 'project_analysis',
    source_project: 'a'.repeat(32),
    target_project: null,
    run_baseline: false,
  })
  expect(submitted).not.toHaveProperty('target')
})

test('editing a pinned project invalidates its selection', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/projects', (route) => route.fulfill({ json: project('source') }))
  await page.goto('/?view=structure')
  await fillPin(page, 'Source')
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeEnabled()
  await page.getByLabel('Source project root', { exact: true }).fill('another-library')
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeDisabled()
  await expect(page.getByText('Ready for feature discovery', { exact: true })).toHaveCount(0)
})

test('unsupported projects show their blocker and cannot queue analysis', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/projects', (route) => route.fulfill({ json: project('source', false) }))
  await page.goto('/?view=structure')
  await fillPin(page, 'Source')
  await expect(page.getByText('No Python project manifest at the selected root.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeDisabled()
  await expect(page.getByText('No analysis available', { exact: true })).toBeVisible()
})

test('native baseline uses the explicitly selected target ID', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/projects', (route) =>
    route.fulfill({ json: project(route.request().postDataJSON().role) }),
  )
  let submitted: Record<string, unknown> | undefined
  await page.route('**/api/runs', (route) => {
    if (route.request().method() === 'POST') submitted = route.request().postDataJSON()
    return route.fulfill({ json: route.request().method() === 'POST' ? fixtureRun : { items: [] } })
  })
  await page.goto('/?view=structure')
  await fillPin(page, 'Source')
  await page.getByLabel('Add a target project').check()
  await expect(page.getByRole('button', { name: 'Analyze projects' })).toBeDisabled()
  await fillPin(page, 'Target')
  await page.getByLabel('Run the target’s original tests offline').check()
  await page.getByRole('button', { name: 'Analyze projects' }).click()
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
  expect(submitted).toMatchObject({
    source_project: 'a'.repeat(32),
    target_project: 'b'.repeat(32),
    run_baseline: true,
  })
})
