import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import type { Run, RunPage } from '../src/api/client'

test('local backend serves recorded analysis and all evidence views without inference', async ({
  page,
  request,
}, testInfo) => {
  const runs = await request.get('/api/runs')
  expect(runs.ok()).toBeTruthy()
  const history = (await runs.json()) as RunPage
  const recorded = history.items.find(
    (run) => run.kind === 'fit_select' && run.outcome === 'selected',
  )
  test.skip(!recorded, 'No recorded successful selection exists in this local workspace.')
  const pageErrors: string[] = []
  const detail = (await (await request.get(`/api/runs/${recorded!.id}`)).json()) as Run
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: 'Break a repository into features.' }),
  ).toBeVisible()
  await expect(page.getByLabel('Add a target project')).toHaveCount(0)
  await expect(page.getByText('Local connection', { exact: true })).toBeVisible()
  await page.screenshot({
    path: `../data/frontend-qa/${testInfo.project.name}-new-run.png`,
    fullPage: true,
  })
  for (const view of ['overview', 'features', 'evidence', 'tests', 'changes', 'exports']) {
    await page.goto(`/runs/${recorded!.id}/${view}`)
    await expect(page.getByRole('heading', { name: recorded!.title, exact: true })).toBeVisible()
    await expect(page.locator('.run-view')).not.toBeEmpty()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      view,
    ).toBe(true)
    if (view === 'overview')
      await page.screenshot({
        path: `../data/frontend-qa/${testInfo.project.name}-overview.png`,
        fullPage: true,
      })
    if (view === 'evidence') {
      await page
        .getByRole('button', { name: /^Inspect .+ lines/ })
        .first()
        .click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await expect(page.getByText('Stored, read-backed excerpt')).toBeVisible()
      await expect(page.locator('.inspector-content')).toHaveCSS('opacity', '1')
      await page.screenshot({
        path: `../data/frontend-qa/${testInfo.project.name}-evidence.png`,
        fullPage: false,
      })
      await page.keyboard.press('Escape')
    }
    if (view === 'exports') {
      const report = detail.artifacts!.find(
        (item) => item.label === 'report.json' && item.available,
      )!
      const downloading = page.waitForEvent('download')
      await page.getByRole('link', { name: 'Download report.json', exact: true }).click()
      const file = await downloading
      expect(file.suggestedFilename()).toBe('report.json')
      const bytes = await readFile((await file.path())!)
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(report.sha256)
    }
  }
  expect(pageErrors).toEqual([])
})
