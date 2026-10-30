import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { ChangeReview, Run } from '../src/api/client'

test('fresh offline Docker adaptation opens through production API review and exact download', async ({
  page,
  request,
}) => {
  test.skip(process.env.GRAFT_RELEASE_DOCKER !== '1', 'Fresh F9 offline Docker evidence only.')
  const url = `/api/runs/${'d'.repeat(32)}`
  const response = await request.get(url)
  expect(response.ok()).toBe(true)
  const before = (await response.json()) as Run
  expect(before.adaptation?.verdict).toBe('verified')
  expect(before.adaptation?.protected_tests_unchanged).toBe(true)
  const reviewResponse = await request.get(`${url}/changes`)
  expect(reviewResponse.ok()).toBe(true)
  const review = (await reviewResponse.json()) as ChangeReview
  expect(review.publishing_enabled).toBe(false)
  expect(review.patch?.sha256).toBe(before.adaptation?.patch_sha256)
  const writes: string[] = []
  page.on('request', (call) => {
    if (call.method() !== 'GET') writes.push(call.method())
  })
  await page.goto(`/runs/${'d'.repeat(32)}/changes`)
  await expect(page.getByRole('heading', { name: 'Verified production patch' })).toBeVisible()
  await page.getByRole('button', { name: /src\/sequence.py/ }).click()
  const diff = page.getByRole('region', { name: 'Unified diff' })
  await expect(diff).toContainText('Permission is hereby granted')
  await diff.focus()
  await expect(diff).toBeFocused()
  const patch = await request.get(`${url}/artifacts/${review.patch!.id}`)
  expect(patch.ok()).toBe(true)
  expect(
    createHash('sha256')
      .update(await patch.body())
      .digest('hex'),
  ).toBe(review.patch!.sha256)
  for (const item of review.evidence) {
    const artifact = await request.get(`${url}/artifacts/${item.id}`)
    expect(artifact.ok(), item.label).toBe(true)
    expect(
      createHash('sha256')
        .update(await artifact.body())
        .digest('hex'),
      item.label,
    ).toBe(item.sha256)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(writes).toEqual([])
  expect(((await (await request.get(url)).json()) as Run).usage).toEqual(before.usage)
})
