import { test, expect, type Page } from '@playwright/test'
import { mockApi, fixtureRun } from './fixtures'
import type { Catalog, CatalogFeature, CatalogPage, ProjectSelection } from '../src/api/client'

const id = 'a'.repeat(32)
const entries: CatalogPage['items'] = Array.from({ length: 28 }, (_, index) => ({
  feature_id: index.toString(16).padStart(32, '0'),
  name: `Feature ${String(index + 1).padStart(2, '0')}`,
  summary: 'An independently discovered capability.',
  category: 'sequences',
  group: 'src',
  parent_feature_id: null,
  aliases: [],
  evidence_status: index < 2 ? 'supported' : 'unverified',
  trace_state: index < 2 ? 'validated' : 'untraced',
  assessment: null,
}))
const catalog: Catalog = {
  id,
  revision: 1,
  base_revision: null,
  run_id: 'catalog-job',
  source_project: 'b'.repeat(32),
  source_sha: 'c'.repeat(40),
  project_root: 'library',
  scope: '',
  identity: {},
  state: 'partial',
  created_at: '2026-10-28T21:00:00Z',
  counts: {
    discovered: 28,
    code_supported: 2,
    partial: 0,
    unsupported: 0,
    unverified: 26,
    deeply_traced: 2,
  },
  options: {
    max_files: 32,
    max_candidates: 500,
    trace_limit: 8,
    max_requests: 32,
    max_input_tokens: 400000,
    max_output_tokens: 40000,
    max_seconds: 1200,
  },
  processed_paths: ['src/sequence.py'],
  remaining_paths: ['src/hidden.py'],
  examined_paths: ['src/sequence.py'],
  excluded_paths: ['.env'],
  unexamined_areas: ['src/hidden.py'],
  limit_reasons: ['job_file_limit', 'deep_trace_shortlist'],
  failure_code: null,
  batches: [],
  usage: { requests: 4 },
  cumulative_usage: { requests: 4 },
  returned_model_ids: ['muse-spark-1.3'],
  continuation_available: true,
  cache_hit: false,
  qualification:
    'All discovered features in the declared scope. A bounded scan cannot prove every capability.',
}

function detail(featureId: string): CatalogFeature {
  return {
    ...entries.find((item) => item.feature_id === featureId)!,
    behavior_key: 'capability',
    discovery_refs: [],
    card: null,
    evidence: [
      {
        ref: { ...fixtureRun.references![0].ref, path: 'src/sequence.py' },
        text: `def unique(items):\n    return list(dict.fromkeys(items))\n# ${'long-code-'.repeat(60)}`,
        sha256: 'd'.repeat(64),
      },
    ],
    examined_paths: ['src/sequence.py'],
    missing_evidence: ['Code corroboration has not been completed.'],
    failure_code: null,
    qualification:
      'Source tests are cited, not executed. Relevance, eligibility and adaptation are unassessed.',
  }
}

async function mockCatalog(page: Page, override: Partial<Catalog> = {}) {
  await mockApi(page)
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const details: string[] = []
  await page.route('**/api/catalogs/**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'POST') {
      writes.push({ path: url.pathname, body: route.request().postDataJSON() })
      return route.fulfill({ json: { ...catalog, revision: 2, base_revision: 1 } })
    }
    if (url.pathname.includes('/features/')) {
      const featureId = url.pathname.split('/').at(-1)!
      details.push(featureId)
      return route.fulfill({ json: detail(featureId) })
    }
    if (url.pathname.endsWith('/features')) {
      const q = url.searchParams.get('q')?.toLowerCase() ?? ''
      const status = url.searchParams.get('status')
      const items =
        override.counts?.discovered === 0
          ? []
          : entries.filter(
              (item) =>
                item.name.toLowerCase().includes(q) && (!status || status === item.evidence_status),
            )
      const start = url.searchParams.has('cursor') ? 25 : 0
      return route.fulfill({
        json: {
          catalog_id: id,
          catalog_revision: 1,
          items: items.slice(start, start + 25),
          counts: { ...catalog.counts, ...override.counts },
          matching_filter: items.length,
          next_cursor: items.length > start + 25 ? 'next-page' : null,
        },
      })
    }
    const revision = Number(url.searchParams.get('revision') ?? 1)
    return route.fulfill({
      json: { ...catalog, ...override, revision, base_revision: revision > 1 ? 1 : null },
    })
  })
  return { writes, details }
}

test('saved catalog pages and lazy evidence reopen without inference', async ({
  page,
}, testInfo) => {
  const { writes, details } = await mockCatalog(page)
  await page.goto(`/catalogs/${id}?revision=1`)
  await expect(page.getByRole('heading', { name: 'Source feature catalog' })).toBeVisible()
  await expect(page.getByText('28 matching features.', { exact: false })).toBeVisible()
  expect(details).toHaveLength(0)
  await expect(page.getByRole('link', { name: 'Feature 28', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Next page' }).click()
  await page.getByRole('link', { name: 'Feature 28', exact: true }).click()
  await expect(
    page
      .getByRole('region', { name: 'Feature detail' })
      .getByRole('heading', { name: 'Feature 28', exact: true }),
  ).toBeFocused()
  expect(details).toHaveLength(1)
  await page
    .getByRole('region', { name: 'Feature detail' })
    .getByText('src/sequence.py:', { exact: false })
    .click()
  await expect(page.getByText('def unique(items):', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({
    path: `../data/frontend-qa/f4-${testInfo.project.name}-catalog.png`,
    fullPage: true,
  })
  await page.reload()
  await expect(
    page
      .getByRole('region', { name: 'Feature detail' })
      .getByRole('heading', { name: 'Feature 28', exact: true }),
  ).toBeVisible()
  expect(writes).toHaveLength(0)
})

test('global search, status filters and Back preserve a revision', async ({ page }) => {
  const { writes } = await mockCatalog(page)
  await page.goto(`/catalogs/${id}?revision=1`)
  await page.getByLabel('Search saved features').fill('Feature 28')
  await expect(page.getByText('1 matching features.', { exact: false })).toBeVisible()
  await expect(page.getByLabel('Search saved features')).toBeFocused()
  await page.getByLabel('Evidence status').selectOption('supported')
  await expect(page.getByRole('heading', { name: 'No matching features' })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('link', { name: 'Feature 28', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/revision=1/)
  expect(writes).toHaveLength(0)
})

test('continuation explicitly creates a new budgeted revision', async ({ page }) => {
  const { writes } = await mockCatalog(page)
  await page.goto(`/catalogs/${id}?revision=1`)
  await page.getByRole('button', { name: 'Continue discovery', exact: true }).click()
  await expect(page).toHaveURL(/revision=2/)
  expect(writes).toHaveLength(1)
  expect(writes[0].body).toMatchObject({
    base_revision: 1,
    options: { max_files: 32, trace_limit: 8 },
  })
  await page.getByRole('link', { name: 'Open previous revision' }).click()
  await expect(page).toHaveURL(/revision=1/)
  expect(writes).toHaveLength(1)
})

test('on-demand corroboration requests only the selected candidate', async ({ page }) => {
  const { writes } = await mockCatalog(page)
  const featureId = entries[2].feature_id
  await page.goto(`/catalogs/${id}?revision=1&feature=${featureId}`)
  await page.getByRole('button', { name: 'Corroborate this feature' }).click()
  await expect(page).toHaveURL(/revision=2/)
  expect(writes[0].body).toMatchObject({
    base_revision: 1,
    trace_feature_ids: [featureId],
    options: { max_files: 0, trace_limit: 1 },
  })
})

test('failed discovery remains visible even with zero features', async ({ page }) => {
  await mockCatalog(page, {
    state: 'failed',
    failure_code: 'meta_request_failed',
    counts: {
      discovered: 0,
      code_supported: 0,
      partial: 0,
      unsupported: 0,
      unverified: 0,
      deeply_traced: 0,
    },
  })
  await page.goto(`/catalogs/${id}?revision=1`)
  await expect(
    page.getByText('Discovery encountered meta request failed.', { exact: false }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No matching features' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue discovery', exact: true })).toBeVisible()
})

test('source discovery is an explicit action on a supported pin', async ({ page }) => {
  await mockCatalog(page)
  const project: ProjectSelection = {
    id: 'b'.repeat(32),
    requested: {
      url: 'https://github.com/team/sequence-tools',
      commit_sha: 'c'.repeat(40),
      role: 'source',
      project_root: '',
    },
    snapshot: fixtureRun.source!,
    support: {
      profile_version: 'python-evidence-v1',
      repo_id: 'source',
      commit_sha: 'c'.repeat(40),
      project_root: '',
      capability: 'evidence_analysis',
      python_files: 3,
      manifest_sha256: 'd'.repeat(64),
      execution_supported: false,
      adaptation_supported: false,
      notice_policy: 'analysis-only-v1',
      adaptation_reason: 'Feature-specific acceptance and allowed-use review are required.',
    },
    inventory_sha256: 'e'.repeat(64),
    root_choices: [''],
    created_at: '2026-10-28T21:00:00Z',
  }
  await page.route('**/api/projects', (route) => route.fulfill({ json: project }))
  let submitted: Record<string, unknown> | undefined
  await page.route('**/api/catalogs', (route) => {
    submitted = route.request().postDataJSON()
    return route.fulfill({ json: catalog })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeDisabled()
  await page.getByLabel('Source public repository').fill(project.requested.url)
  await page.getByLabel('Source commit SHA').fill(project.requested.commit_sha)
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await page.getByLabel('Features to corroborate deeply').fill('2')
  await page.getByRole('button', { name: 'Discover features', exact: true }).click()
  await expect(page).toHaveURL(/catalogs\/.*revision=1/)
  expect(submitted).toMatchObject({
    source_project: project.id,
    options: { max_files: 32, trace_limit: 2 },
  })
})
