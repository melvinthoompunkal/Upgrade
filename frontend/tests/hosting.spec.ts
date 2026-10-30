import { test, expect } from '@playwright/test'
import { capabilities, fixtureRun, mockApi } from './fixtures'

const hosted = {
  ...capabilities,
  deployment_mode: 'hosted',
  kinds: ['project_analysis', 'catalog_discovery', 'relevance_assessment'],
  target_execution_supported: false,
  patch_supported: false,
  image_receipt_present: false,
}

test('hosted service disables local diagnostics even when a worker and model are available', async ({
  page,
}) => {
  await mockApi(page)
  await page.route('**/api/capabilities', (route) => route.fulfill({ json: hosted }))
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST') writes.push(request.url())
  })
  await page.goto('/system')
  await expect(page.getByRole('heading', { name: 'Analysis worker' })).toBeVisible()
  await expect(page.getByText('Target execution unavailable', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Meta check', exact: true })).toBeDisabled()
  await expect(
    page.getByRole('button', { name: 'Test committed target', exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByRole('button', { name: 'Test local fixture files', exact: true }),
  ).toBeDisabled()
  await page.goto('/example')
  await expect(
    page.getByRole('button', { name: 'Find a suitable feature', exact: true }),
  ).toBeDisabled()
  expect(writes).toEqual([])
})

test('hosted source analysis cannot accidentally request target execution', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/capabilities', (route) => route.fulfill({ json: hosted }))
  await page.route('**/api/projects', (route) => {
    const role = route.request().postDataJSON().role
    return route.fulfill({
      json: {
        id: (role === 'source' ? 'a' : 'b').repeat(32),
        requested: {
          role,
          url: 'https://github.com/team/library',
          commit_sha: 'c'.repeat(40),
          project_root: '',
        },
        snapshot: { ...fixtureRun.source, commit_sha: 'c'.repeat(40) },
        support: {
          capability: 'evidence_analysis',
          execution_supported: true,
          reasons: [],
          python_files: 1,
        },
        root_choices: [''],
        inventory_sha256: 'd'.repeat(64),
        created_at: fixtureRun.created_at,
      },
    })
  })
  let submitted: Record<string, unknown> | undefined
  await page.route('**/api/runs', (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: { items: [] } })
    submitted = route.request().postDataJSON()
    return route.fulfill({ json: { ...fixtureRun, kind: 'project_analysis' } })
  })
  await page.goto('/')
  for (const role of ['Source', 'Target']) {
    if (role === 'Target') await page.getByLabel('Add a target project').check()
    await page.getByLabel(`${role} public repository`).fill('https://github.com/team/library')
    await page.getByLabel(`${role} commit SHA`).fill('c'.repeat(40))
    await page.getByRole('button', { name: `Inspect ${role.toLowerCase()} project` }).click()
  }
  await expect(page.getByLabel('Run the target’s original tests offline')).toBeDisabled()
  await expect(page.getByText('Target tests are unavailable on this deployment.')).toBeVisible()
  await page.getByRole('button', { name: 'Analyze projects', exact: true }).click()
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
  expect(submitted?.run_baseline).toBe(false)
})

test('missing hosted approval disables job actions while saved views remain accessible', async ({
  page,
}) => {
  await mockApi(page)
  await page.route('**/api/capabilities', (route) =>
    route.fulfill({ json: { ...hosted, kinds: [] } }),
  )
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Analyze projects', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeDisabled()
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByRole('heading', { name: 'Test fixture: crawler recovery' })).toBeVisible()
})
