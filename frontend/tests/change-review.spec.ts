import { test, expect, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { fixtureRun, mockApi } from './fixtures'
import type { ChangeReview, DiffPage, Run } from '../src/api/client'

const patch =
  'diff --git a/src/sequence.py b/src/sequence.py\n--- a/src/sequence.py\n+++ b/src/sequence.py\n@@ -1 +1 @@\n-old\n+new\n'
const sha = createHash('sha256').update(patch).digest('hex')
const artifact = {
  id: 'patch',
  label: 'rounds/round-0.patch',
  kind: 'evidence',
  size: patch.length,
  sha256: sha,
  available: true,
}
const review: ChangeReview = {
  verdict: 'verified',
  outcome: 'verified',
  protected_tests_unchanged: true,
  original_target_unchanged: true,
  repair_count: 0,
  patch: artifact,
  mapping: null,
  files: [
    {
      id: 'file-one',
      path: 'src/sequence.py',
      status: 'modified',
      before_sha256: 'a'.repeat(64),
      after_sha256: 'b'.repeat(64),
      additions: 210,
      deletions: 1,
      diff_lines: 410,
    },
    {
      id: 'file-two',
      path: 'src/formatting.py',
      status: 'added',
      before_sha256: null,
      after_sha256: 'c'.repeat(64),
      additions: 2,
      deletions: 0,
      diff_lines: 6,
    },
  ],
  evidence: [
    { ...artifact, id: 'notice', label: 'notices/source-NOTICE.txt', kind: 'source notice' },
  ],
  publishing_enabled: false,
}

async function setup(page: Page, value = review) {
  const run: Run = {
    ...structuredClone(fixtureRun),
    kind: 'verified_adaptation',
    outcome: value.outcome,
    fit: null,
    assessment: null,
    artifacts: [artifact, ...value.evidence],
    adaptation: {
      schema_version: 'verified-adaptation-v1',
      feature_id: 'f'.repeat(32),
      verdict: value.verdict,
      repair_count: 0,
      protected_tests_unchanged: true,
      changed_files: value.files.length,
      changed_lines: 213,
      new_files: 1,
      patch_artifact: value.patch?.label,
      patch_sha256: value.patch?.sha256,
      rounds: [],
    },
  }
  await mockApi(page, run)
  const requests: string[] = []
  await page.route('**/api/runs/fixture-run/changes**', async (route) => {
    const url = new URL(route.request().url())
    requests.push(url.pathname + url.search)
    if (url.pathname.endsWith('/changes')) return route.fulfill({ json: value })
    const file = value.files.find((item) => url.pathname.endsWith(item.id))!
    const offset = Number(url.searchParams.get('offset') ?? '0')
    const data: DiffPage = {
      file,
      patch_sha256: sha,
      offset,
      total_lines: file.diff_lines,
      next_offset: offset + 200 < file.diff_lines ? offset + 200 : null,
      lines: Array.from({ length: Math.min(200, file.diff_lines - offset) }, (_, i) => ({
        kind: 'addition',
        text: `+${file.path} line ${i + offset + 1}`,
        new_line: i + offset + 1,
        old_line: null,
        truncated: false,
      })),
    }
    data.lines[0].text = '+<script>window.reviewInjected = true</script>'
    await route.fulfill({ json: data })
  })
  await page.route('**/api/runs/fixture-run/artifacts/patch', (route) =>
    route.fulfill({
      body: patch,
      contentType: 'application/octet-stream',
      headers: { 'Content-Disposition': 'attachment; filename="verified.patch"' },
    }),
  )
  return requests
}

test('change review loads files lazily, bounds pages, preserves URL history and exact downloads', async ({
  page,
}) => {
  const requests = await setup(page)
  let writes = 0
  page.on('request', (request) => {
    if (request.method() === 'POST') writes++
  })
  await page.goto('/runs/fixture-run/changes')
  await expect(page.getByRole('heading', { name: 'Verified production patch' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Choose a file to review' })).toBeVisible()
  expect(requests.filter((path) => path.includes('/files/'))).toHaveLength(0)
  await page.getByRole('button', { name: /src\/sequence.py/ }).click()
  await expect(page.getByRole('region', { name: 'Unified diff' })).toBeVisible()
  await expect(page).toHaveURL(/file=file-one/)
  expect(await page.locator('.change-diff tbody tr').count()).toBe(200)
  await page.getByRole('button', { name: 'Next diff page' }).click()
  await expect(page).toHaveURL(/offset=200/)
  await expect(page.getByRole('status').filter({ hasText: 'Diff lines' })).toContainText(
    'Diff lines 201–400 of 410',
  )
  await expect(page.getByRole('heading', { name: 'src/sequence.py', exact: true })).toBeFocused()
  expect(await page.locator('.change-diff tbody tr').count()).toBe(200)
  await page.goBack()
  await expect(page.getByRole('status').filter({ hasText: 'Diff lines' })).toContainText(
    'Diff lines 1–200 of 410',
  )
  await page.reload()
  await expect(page.getByRole('region', { name: 'Unified diff' })).toBeVisible()
  expect(await page.evaluate(() => 'reviewInjected' in window)).toBe(false)
  const href = await page.getByRole('link', { name: 'Download exact patch' }).getAttribute('href')
  const exported = await page.evaluate(async (url) => (await fetch(url!)).text(), href)
  expect(createHash('sha256').update(exported).digest('hex')).toBe(sha)
  await expect(page.getByRole('link', { name: 'Download notices/source-NOTICE.txt' })).toBeVisible()
  await expect(
    page.getByText('Pull request publishing is disabled.', { exact: false }),
  ).toBeVisible()
  expect(writes).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/f8-change-review-${test.info().project.name}.png`,
    fullPage: true,
  })
})

test('keyboard file selection, evidence inspection and invalid deep links recover', async ({
  page,
}) => {
  await setup(page)
  await page.goto('/runs/fixture-run/changes?file=foreign')
  await expect(
    page.getByText('This file is not in the saved patch.', { exact: false }),
  ).toBeVisible()
  const file = page.getByRole('button', { name: /src\/formatting.py/ })
  await file.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'src/formatting.py', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Inspect', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Inspect', exact: true })).toBeFocused()
  await page.goto('/runs/fixture-run/changes?file=file-one&offset=bad')
  await expect(page.getByText('Invalid diff page.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'First diff page' }).click()
  await expect(page.getByRole('region', { name: 'Unified diff' })).toBeVisible()
})

test('failed integrity check can be retried without showing a verified patch', async ({ page }) => {
  await setup(page)
  await page.route('**/api/runs/fixture-run/changes', (route) =>
    route.fulfill({ status: 409, json: { detail: 'Saved patch changed. Check exports.' } }),
  )
  await page.goto('/runs/fixture-run/changes')
  await expect(page.getByText('Saved patch changed. Check exports.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Download exact patch' })).toHaveCount(0)
  await page.unroute('**/api/runs/fixture-run/changes')
  await page.getByRole('button', { name: 'Retry change review' }).click()
  await expect(page.getByRole('heading', { name: 'Verified production patch' })).toBeVisible()
})

test('unverified patches and already-present outcomes keep their actual verdict', async ({
  page,
}) => {
  await setup(page, { ...review, verdict: 'unverified', outcome: 'exhausted' })
  await page.goto('/runs/fixture-run/changes')
  await expect(page.getByRole('heading', { name: 'Unverified production patch' })).toBeVisible()
  await expect(
    page.getByText('This run did not establish a verified adaptation.', { exact: false }),
  ).toBeVisible()
  await setup(page, {
    ...review,
    verdict: 'already_present',
    outcome: 'already_present',
    patch: null,
    files: [],
  })
  await page.reload()
  await expect(
    page.getByRole('heading', { name: 'This behavior is already present' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No patch needed' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Download exact patch' })).toHaveCount(0)
})

test('stale file responses cannot replace the newly selected file', async ({ page }) => {
  await setup(page)
  let release: () => void = () => {}
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let started: () => void = () => {}
  const requested = new Promise<void>((resolve) => {
    started = resolve
  })
  await page.route('**/changes/files/file-one?*', async (route) => {
    started()
    await pending
    await route.fulfill({ status: 409, json: { detail: 'Stale file failure' } }).catch(() => {})
  })
  await page.goto('/runs/fixture-run/changes')
  await page.getByRole('button', { name: /src\/sequence.py/ }).click()
  await requested
  await page.getByRole('button', { name: /src\/formatting.py/ }).click()
  await expect(page.getByRole('region', { name: 'Unified diff' })).toBeVisible()
  release()
  await expect(page.getByRole('heading', { name: 'src/formatting.py', exact: true })).toBeVisible()
  await expect(page.getByText('Stale file failure')).toHaveCount(0)
})
