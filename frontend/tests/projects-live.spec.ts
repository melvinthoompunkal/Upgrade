import { test, expect } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { Run } from '../src/api/client'

test('real public source and target analysis needs no model and retains citations', async ({
  page,
  request,
}, testInfo) => {
  test.skip(!process.env.F3_LIVE_PUBLIC, 'Explicit public GitHub fetch verification only.')
  test.setTimeout(90_000)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/?view=structure')
  await page.getByLabel('Source public repository').fill('https://github.com/pallets/itsdangerous')
  await page.getByLabel('Source revision type').selectOption('commit')
  await page.getByLabel('Source commit SHA').fill('672971d66a2ef9f85151e53283113f33d642dabd')
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await expect(page.getByText('itsdangerous', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('Add a target project').check()
  await page
    .getByLabel('Target public repository')
    .fill('https://github.com/more-itertools/more-itertools')
  await page.getByLabel('Target revision type').selectOption('commit')
  await page.getByLabel('Target commit SHA').fill('8c1a6ef241b51ff055e89219f050ccf4f15f37f6')
  await page.getByRole('button', { name: 'Inspect target project' }).click()
  await expect(page.getByText('more-itertools', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page
    .getByLabel('Goal or run title (optional)')
    .fill('F3 independent public project analysis')
  await page.getByRole('button', { name: 'Analyze projects' }).click()
  await expect(page).toHaveURL(/runs\/[a-f0-9]{32}\/overview/)
  await expect(page.getByRole('heading', { name: 'Project analysis complete' })).toBeVisible({
    timeout: 30_000,
  })
  const runId = new URL(page.url()).pathname.split('/')[2]
  const run = (await (await request.get(`/api/runs/${runId}`)).json()) as Run
  expect(run.outcome).toBe('passed')
  expect(run.usage?.requests).toBe(0)
  expect(run.calls).toEqual([])
  expect(run.project_analyses).toHaveLength(2)
  expect(run.source?.commit_sha).toBe('672971d66a2ef9f85151e53283113f33d642dabd')
  expect(run.target?.commit_sha).toBe('8c1a6ef241b51ff055e89219f050ccf4f15f37f6')
  expect(run.project_analyses?.every((analysis) => analysis.entry_points.length > 0)).toBe(true)
  await page
    .getByRole('button', { name: /^Inspect / })
    .first()
    .click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('Stored, read-backed excerpt')).toBeVisible()
  await page.keyboard.press('Escape')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: `../data/frontend-qa/f3-${testInfo.project.name}-live-result.png` })
  expect(errors).toEqual([])
  await writeFile(
    testInfo.outputPath('public-project-analysis.json'),
    JSON.stringify(
      {
        run_id: runId,
        outcome: run.outcome,
        source: run.source,
        target: run.target,
        project_ids: run.project_analyses?.map((analysis) => analysis.project.id),
        references: run.references?.length,
        usage: run.usage,
        artifacts: run.artifacts,
      },
      null,
      2,
    ),
  )
})
