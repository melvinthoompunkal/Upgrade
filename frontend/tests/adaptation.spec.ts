import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import { fixtureRun, mockApi } from './fixtures'
import type { Run } from '../src/api/client'

const patchText =
  'diff --git a/src/api.py b/src/api.py\n--- a/src/api.py\n+++ b/src/api.py\n@@ -1 +1 @@\n-old\n+new\n'
const patchDigest = createHash('sha256').update(patchText).digest('hex')

function adaptation(verdict: 'verified' | 'unverified'): Run {
  return {
    ...structuredClone(fixtureRun),
    kind: 'verified_adaptation',
    outcome: verdict === 'verified' ? 'verified' : 'exhausted',
    title: 'Empty sequence adaptation',
    fit: null,
    assessment: null,
    adaptation: {
      schema_version: 'verified-adaptation-v1',
      feature_id: 'a'.repeat(32),
      verdict,
      repair_count: verdict === 'verified' ? 1 : 3,
      protected_tests_unchanged: true,
      patch_artifact: 'rounds/round-1.patch',
      patch_sha256: patchDigest,
      changed_files: 1,
      changed_lines: 30,
      new_files: 0,
      rounds: [],
    },
    tests: ['before', 'round-1'].map((phase) => ({
      phase,
      suite: 'project_acceptance',
      status: phase === 'before' ? 'failed' : 'passed',
      elapsed_ms: 10,
      image_id: 'sha256:' + 'd'.repeat(64),
      diagnostic: '',
      expected_diagnostic: phase === 'before',
      cases: [
        {
          name: 'test_empty',
          outcome: phase === 'before' ? 'failed' : 'passed',
          message: '',
          detail: '',
        },
      ],
    })),
    artifacts: [
      {
        id: 'patch',
        label: 'rounds/round-1.patch',
        kind: 'evidence',
        size: 300,
        sha256: patchDigest,
        available: true,
      },
    ],
  }
}

test('verified adaptation shows protected tests, phased captures and patch exports', async ({
  page,
}) => {
  await mockApi(page, adaptation('verified'))
  const downloadRoute = (route: import('@playwright/test').Route) =>
    route.fulfill({
      body: patchText,
      contentType: 'application/octet-stream',
      headers: { 'Content-Disposition': 'attachment; filename="verified.patch"' },
    })
  await page.route('**/api/runs/fixture-run/artifacts/patch', downloadRoute)
  await page.goto('/runs/fixture-run/overview')
  await expect(page.getByRole('heading', { name: 'The patch passed verification.' })).toBeVisible()
  await expect(page.getByText('Unchanged and hash checked')).toBeVisible()
  await page.getByRole('link', { name: 'Test evidence', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'before: Frozen feature acceptance' }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'round-1: Frozen feature acceptance' }),
  ).toBeVisible()
  await page.getByRole('link', { name: 'Exports', exact: true }).click()
  await expect(page.getByText('rounds/round-1.patch', { exact: true })).toBeVisible()
  const download = page.getByRole('link', { name: 'Download rounds/round-1.patch' })
  await expect(download).toHaveAttribute('href', '/api/runs/fixture-run/artifacts/patch')
  await expect(download).toHaveAttribute('download', '')
  // Native Chrome downloads bypass Playwright routes; fetch the same link to inspect bytes.
  const exported = await page.evaluate(
    async (url) => {
      const response = await fetch(url!)
      return {
        status: response.status,
        body: await response.text(),
        disposition: response.headers.get('Content-Disposition'),
      }
    },
    await download.getAttribute('href'),
  )
  expect(exported.status).toBe(200)
  expect(exported.disposition).toContain('verified.patch')
  expect(createHash('sha256').update(exported.body).digest('hex')).toBe(patchDigest)
})

test('exhausted adaptation is explicitly unverified on reopen', async ({ page }) => {
  await mockApi(page, adaptation('unverified'))
  await page.goto('/runs/fixture-run/overview')
  await expect(
    page.getByRole('heading', { name: 'The adaptation remains unverified.' }),
  ).toBeVisible()
  await expect(page.getByText('Repair allowance exhausted')).toBeVisible()
  await page.reload()
  await expect(
    page.getByRole('heading', { name: 'The adaptation remains unverified.' }),
  ).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
