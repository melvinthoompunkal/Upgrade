import { test, expect, type Page } from '@playwright/test'
import { fixtureRun, mockApi } from './fixtures'
import type { ChangeReview, Publication, PublishReview, Run } from '../src/api/client'

const patchSha = 'c7'.repeat(32)
const noticesSha = 'b3'.repeat(32)
const receipt: Publication = {
  repository_id: 101,
  repository: 'team/sequence-target',
  base_branch: 'main',
  base_sha: '7a'.repeat(20),
  branch: 'upgrade/retained-publication',
  patch_sha256: patchSha,
  notices_sha256: noticesSha,
  created_at: '2026-10-30T15:00:00+00:00',
  state: 'published',
  tree_sha: 'd'.repeat(40),
  commit_sha: 'e'.repeat(40),
  pr_number: 23,
  pr_url: 'https://github.com/team/sequence-target/pull/23',
  failure_code: null,
}

async function setup(page: Page, failure?: 'retryable' | 'stale' | 'denied') {
  const run: Run = {
    ...structuredClone(fixtureRun),
    title: 'Sequence grouping adaptation',
    kind: 'verified_adaptation',
    outcome: 'verified',
    fit: null,
    assessment: null,
    target: {
      ...fixtureRun.target!,
      origin: 'https://github.com/team/sequence-target',
      commit_sha: receipt.base_sha,
    },
  }
  await mockApi(page, run)
  const review: ChangeReview = {
    verdict: 'verified',
    outcome: 'verified',
    protected_tests_unchanged: true,
    original_target_unchanged: true,
    repair_count: 0,
    publishing_enabled: true,
    patch: {
      id: 'patch',
      label: 'rounds/round-0.patch',
      kind: 'evidence',
      size: 1543,
      sha256: patchSha,
      available: true,
    },
    mapping: null,
    files: [
      {
        id: 'sequence',
        path: 'src/sequence.py',
        status: 'modified',
        before_sha256: 'a'.repeat(64),
        after_sha256: 'b'.repeat(64),
        additions: 73,
        deletions: 3,
        diff_lines: 88,
      },
    ],
    evidence: [
      {
        id: 'notice',
        label: 'notices/source-NOTICE.txt',
        kind: 'source notice',
        size: 1237,
        sha256: noticesSha,
        available: true,
      },
    ],
  }
  await page.route('**/api/runs/fixture-run/changes', (route) => route.fulfill({ json: review }))
  let publication: Publication | null = null
  const writes: { key: string | undefined; body: Record<string, unknown> }[] = []
  const view = (): PublishReview => ({
    enabled: true,
    reason: 'Review the exact patch and notices before creating a draft pull request.',
    target_origin: 'https://github.com/team/sequence-target',
    base_sha: receipt.base_sha,
    patch_sha256: patchSha,
    notices_sha256: noticesSha,
    repository_id: 101,
    base_branch: 'main',
    publication,
  })
  await page.route('**/api/runs/fixture-run/pull-request', async (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: view() })
    writes.push({
      key: route.request().headers()['idempotency-key'],
      body: route.request().postDataJSON(),
    })
    if (writes.length === 1 && failure) {
      if (failure !== 'denied')
        publication = { ...receipt, state: failure, pr_url: null, pr_number: null }
      return route.fulfill({
        status: failure === 'denied' ? 403 : 409,
        json: {
          detail:
            failure === 'stale'
              ? 'The base branch moved. Revalidate the adaptation before publishing.'
              : failure === 'denied'
                ? 'Publishing needs current user and App write rights on the public target.'
                : 'GitHub did not return a usable response. Try again later.',
        },
      })
    }
    publication = structuredClone(receipt)
    return route.fulfill({ json: publication })
  })
  return writes
}

test('publishing requires explicit review and retains a draft PR after reopening', async ({
  page,
}, testInfo) => {
  const writes = await setup(page)
  await page.goto('/runs/fixture-run/changes')
  const panel = page.getByRole('region', { name: 'Publish reviewed patch' })
  await expect(panel.getByText(/sequence-target/)).toBeVisible()
  await expect(panel.getByLabel('Base branch')).toHaveValue('main')
  const create = panel.getByRole('button', { name: 'Create draft pull request' })
  await expect(create).toBeDisabled()
  expect(writes).toHaveLength(0)
  await testInfo.attach('publishing-form', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })
  const reviewed = panel.getByRole('checkbox')
  await reviewed.focus()
  await page.keyboard.press('Space')
  await expect(create).toBeEnabled()
  await create.focus()
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('heading', { name: 'Draft pull request created' })).toBeVisible()
  await expect(panel.getByRole('link', { name: 'Open pull request #23' })).toHaveAttribute(
    'href',
    receipt.pr_url!,
  )
  expect(writes).toHaveLength(1)
  expect(writes[0].body).toEqual({
    repository_id: 101,
    base_branch: 'main',
    patch_sha256: patchSha,
    notices_sha256: noticesSha,
    reviewed: true,
  })
  expect(writes[0].key).toBeTruthy()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Draft pull request created' })).toBeVisible()
  expect(writes).toHaveLength(1)
  await page.getByText('Publication hashes', { exact: true }).click()
  await expect(page.getByText(noticesSha, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await testInfo.attach('publishing-review', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })
})

test('an explicit retry recovers the same publication key after a provider failure', async ({
  page,
}) => {
  const writes = await setup(page, 'retryable')
  await page.goto('/runs/fixture-run/changes')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Create draft pull request' }).click()
  await expect(
    page.getByText('GitHub did not return a usable response. Try again later.'),
  ).toBeVisible()
  const retry = page.getByRole('button', { name: 'Retry draft pull request' })
  await expect(retry).toBeEnabled()
  expect(writes).toHaveLength(1)
  await retry.click()
  await expect(page.getByRole('heading', { name: 'Draft pull request created' })).toBeVisible()
  expect(writes).toHaveLength(2)
  expect(writes[1]).toEqual(writes[0])
})

test('a moved base requires revalidation and cannot be retried as a fresh write', async ({
  page,
}) => {
  const writes = await setup(page, 'stale')
  await page.goto('/runs/fixture-run/changes')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Create draft pull request' }).click()
  await expect(page.getByRole('link', { name: 'Start a fresh adaptation' })).toBeVisible()
  await expect(page.getByRole('button', { name: /draft pull request/ })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('link', { name: 'Start a fresh adaptation' })).toBeVisible()
  expect(writes).toHaveLength(1)
})

test('permission denial is actionable and never presents a successful publication', async ({
  page,
}) => {
  const writes = await setup(page, 'denied')
  await page.goto('/runs/fixture-run/changes')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Create draft pull request' }).click()
  await expect(
    page.getByText('Publishing needs current user and App write rights on the public target.'),
  ).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open pull request #23' })).toHaveCount(0)
  expect(writes).toHaveLength(1)
})
