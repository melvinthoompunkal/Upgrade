import { test, expect } from '@playwright/test'
import { fixtureRun, mockApi } from './fixtures'

test('cancel waits for the controller and keeps the run inspectable', async ({
  page,
}, testInfo) => {
  const run = structuredClone(fixtureRun)
  run.status = 'running'
  run.outcome = null
  let cancellations = 0
  await mockApi(page, run)
  await page.route('**/api/runs/fixture-run/cancel', async (route) => {
    cancellations++
    run.cancel_requested = true
    run.revision++
    await route.fulfill({ json: run })
  })
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByText('Leaving this page keeps the run active.')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel run', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'Cancellation requested' })).toBeDisabled()
  expect(cancellations).toBe(1)
  await page.screenshot({ path: `../data/frontend-qa/f6-${testInfo.project.name}-cancel.png` })
  run.status = 'finished'
  run.outcome = 'cancelled'
  run.revision++
  await expect(page.getByRole('button', { name: 'Cancellation requested' })).toHaveCount(0)
  await expect(
    page.getByRole('navigation', { name: 'Run views' }).or(page.locator('.run-tabs')),
  ).toBeVisible()
})

test('storage deletion requires review and explicit confirmation', async ({ page }, testInfo) => {
  await mockApi(page)
  let deleted = 0
  await page.route('**/api/usage', (route) =>
    route.fulfill({
      json: {
        unfinished_jobs: 0,
        owner_job_limit: 4,
        reserved_bytes: 209715200,
        storage_limit_bytes: 2147483648,
        retention_days: 30,
        daily_usage: { model_attempts: 3 },
        daily_limits: { model_attempts: 128 },
        private_model_analysis_enabled: false,
        hosted_execution_enabled: false,
      },
    }),
  )
  await page.route('**/api/privacy/data', async (route) => {
    expect(route.request().method()).toBe('DELETE')
    deleted++
    await route.fulfill({ json: { id: 'a'.repeat(32), state: 'complete', deleted_count: 2 } })
  })
  await page.goto('/system')
  await expect(page.getByRole('button', { name: 'Delete saved workspace data' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Review storage and limits' }).click()
  const remove = page.getByRole('button', { name: 'Delete saved workspace data' })
  await expect(remove).toBeDisabled()
  await expect(page.getByText('3 / 128 today (UTC)')).toBeVisible()
  await page.getByRole('checkbox', { name: /Delete my saved runs/ }).focus()
  await page.keyboard.press('Space')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `../data/frontend-qa/f6-${testInfo.project.name}-storage.png` })
  await remove.click()
  await expect(page.getByText(/Deletion complete. Removed 2 storage directories/)).toBeVisible()
  expect(deleted).toBe(1)
})

test('unfinished jobs block data deletion', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/usage', (route) =>
    route.fulfill({
      json: {
        unfinished_jobs: 1,
        owner_job_limit: 4,
        reserved_bytes: 209715200,
        storage_limit_bytes: 2147483648,
        retention_days: 30,
        daily_usage: {},
        daily_limits: {},
        private_model_analysis_enabled: false,
        hosted_execution_enabled: false,
      },
    }),
  )
  await page.goto('/system')
  await page.getByRole('button', { name: 'Review storage and limits' }).click()
  await expect(page.getByRole('checkbox', { name: /Delete my saved runs/ })).toBeDisabled()
  await expect(page.getByText('Finish or cancel your jobs before deleting data.')).toBeVisible()
})
