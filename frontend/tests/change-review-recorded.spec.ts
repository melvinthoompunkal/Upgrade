import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { ChangeReview, Run } from '../src/api/client'

test('retained F7 execution receipt opens through real change review without inference', async ({
  page,
  request,
}) => {
  const runId = process.env.F8_RECORDED_REVIEW
  test.skip(!runId, 'Explicit retained-receipt review; seed an isolated local backend first.')
  let writes = 0
  page.on('request', (call) => {
    if (call.method() === 'POST') writes++
  })
  const before = await request.get(`/api/runs/${runId}`)
  expect(before.ok()).toBeTruthy()
  const run = (await before.json()) as Run
  expect(run.adaptation?.verdict).toBe('verified')
  const response = await request.get(`/api/runs/${runId}/changes`)
  expect(response.ok()).toBeTruthy()
  const review = (await response.json()) as ChangeReview
  expect(review.patch?.sha256).toBe(run.adaptation?.patch_sha256)
  expect(review.files).toHaveLength(1)
  await page.goto(`/runs/${runId}/changes`)
  await expect(page.getByRole('heading', { name: 'Verified production patch' })).toBeVisible()
  await page.getByRole('button', { name: /src\/sequence.py/ }).click()
  await expect(page.getByRole('region', { name: 'Unified diff' })).toContainText(
    'Permission is hereby granted',
  )
  const download = await request.get(`/api/runs/${runId}/artifacts/${review.patch!.id}`)
  expect(
    createHash('sha256')
      .update(await download.body())
      .digest('hex'),
  ).toBe(review.patch!.sha256)
  const notice = review.evidence.find((item) => item.label === 'notices/source-NOTICE.txt')!
  const notices = await request.get(`/api/runs/${runId}/artifacts/${notice.id}`)
  expect(await notices.text()).toContain('Permission is hereby granted')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(writes).toBe(0)
  const after = await request.get(`/api/runs/${runId}`)
  expect((await after.json()).usage).toEqual(run.usage)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({
    path: `../data/f8-recorded-review-${test.info().project.name}.png`,
    fullPage: true,
  })
})
