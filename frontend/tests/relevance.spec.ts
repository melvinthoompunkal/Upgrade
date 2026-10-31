import { test, expect, type Page } from '@playwright/test'
import { mockApi, fixtureRun } from './fixtures'
import type {
  Assessment,
  Catalog,
  CatalogFeature,
  CatalogPage,
  FeatureAssessment,
} from '../src/api/client'

const catalogId = 'a'.repeat(32)
const assessmentId = 'e'.repeat(32)
const counts = {
  discovered: 10000,
  code_supported: 8,
  partial: 0,
  unsupported: 0,
  unverified: 9992,
  deeply_traced: 8,
}
const entries: CatalogPage['items'] = Array.from({ length: 10000 }, (_, index) => ({
  feature_id: index.toString(16).padStart(32, '0'),
  name: `Capability ${String(index + 1).padStart(5, '0')}`,
  summary: 'A behavior backed by pinned repository evidence.',
  category: 'sequences',
  group: 'library',
  parent_feature_id: null,
  aliases: [],
  evidence_status: 'supported',
  trace_state: 'validated',
  assessment: {
    relevance_status: index < 3 ? 'relevant' : index < 8 ? 'less_relevant' : 'unassessed',
    relevance_score: index < 3 ? 5 : index < 8 ? 1 : null,
    reason: index < 8 ? 'Saved judgment for this goal.' : '',
    target_status: 'unknown',
    fit_status: index < 8 ? 'ineligible' : 'unassessed',
  },
}))
const catalog: Catalog = {
  id: catalogId,
  revision: 1,
  base_revision: null,
  run_id: 'discovery',
  source_project: 'b'.repeat(32),
  source_sha: 'c'.repeat(40),
  project_root: '',
  scope: '',
  identity: {},
  state: 'partial',
  created_at: '2026-10-28T21:00:00Z',
  counts,
  options: {
    max_files: 32,
    max_candidates: 500,
    trace_limit: 8,
    max_requests: 32,
    max_input_tokens: 400000,
    max_output_tokens: 40000,
    max_seconds: 1200,
  },
  processed_paths: [],
  remaining_paths: [],
  examined_paths: [],
  excluded_paths: [],
  unexamined_areas: [],
  limit_reasons: ['deep_trace_shortlist'],
  failure_code: null,
  batches: [],
  usage: {},
  cumulative_usage: {},
  returned_model_ids: [],
  continuation_available: false,
  cache_hit: false,
  qualification: 'Bounded catalog scope.',
}
const assessment: Assessment = {
  id: assessmentId,
  catalog_id: catalogId,
  catalog_revision: 1,
  run_id: 'assessment-job',
  target_project: null,
  target_sha: null,
  target_root: null,
  target_origin: null,
  goal: 'Remove duplicate sequences',
  identity: {},
  options: {
    feature_limit: 8,
    max_requests: 32,
    max_input_tokens: 400000,
    max_output_tokens: 40000,
    max_seconds: 1200,
  },
  shortlist: entries.slice(0, 8).map((item) => item.feature_id),
  base_assessment: null,
  state: 'partial',
  created_at: '2026-10-28T21:00:00Z',
  counts: {
    discovered: 10000,
    deeply_assessed: 8,
    relevant: 3,
    eligible: 0,
    assessment_failed: 0,
    unassessed: 9992,
  },
  top3: [],
  expansion_top3: [],
  limit_reasons: ['deep_assessment_shortlist'],
  usage: { requests: 8 },
  cumulative_usage: { requests: 8 },
  returned_model_ids: [],
  failure_code: null,
  qualification: 'Ratings 3–5 are relevant; judgments are not probabilities.',
}

async function setup(page: Page) {
  await mockApi(page)
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const queries: URLSearchParams[] = []
  let current = assessment
  await page.route('**/api/relevance-assessments**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      writes.push({ path: url.pathname, body })
      current = { ...assessment, id: (writes.length === 1 ? 'f' : 'd').repeat(32), goal: body.goal }
      return route.fulfill({ json: current })
    }
    if (url.pathname.includes('/features/')) {
      const featureId = url.pathname.split('/').at(-1)!
      const saved: FeatureAssessment = {
        feature_id: featureId,
        relevance_status: 'relevant',
        relevance_score: 5,
        reason: 'Useful but no frozen acceptance exists.',
        target_status: 'already_present',
        fit_status: 'ineligible',
        fit: null,
        gates: [
          {
            name: 'acceptance_readiness',
            status: 'unknown',
            reason: 'Frozen acceptance is absent.',
            evidence: [],
          },
        ],
        rejections: ['Frozen acceptance is absent.'],
        judgment: null,
        expansion: null,
        failure_code: null,
        evidence: [],
        evidence_limit_reached: false,
      }
      return route.fulfill({ json: saved })
    }
    return route.fulfill({ json: current })
  })
  await page.route('**/api/catalogs/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.includes('/features/')) {
      const item = entries.find((item) => item.feature_id === url.pathname.split('/').at(-1))!
      const detail: CatalogFeature = {
        ...item,
        behavior_key: 'capability',
        discovery_refs: [],
        card: null,
        evidence: [],
        examined_paths: [],
        missing_evidence: [],
        failure_code: null,
        qualification: 'Saved source evidence.',
      }
      return route.fulfill({ json: detail })
    }
    if (url.pathname.endsWith('/features')) {
      queries.push(url.searchParams)
      const q = url.searchParams.get('q')?.toLowerCase() ?? ''
      const mode = url.searchParams.get('mode')
      const status = url.searchParams.get('relevance_status')
      const fit = url.searchParams.get('fit_status')
      const items = entries.filter(
        (item) =>
          item.name.toLowerCase().includes(q) &&
          (mode !== 'relevant' || item.assessment?.relevance_status === 'relevant') &&
          (!status || item.assessment?.relevance_status === status) &&
          (!fit || item.assessment?.fit_status === fit),
      )
      const start = Number(url.searchParams.get('cursor') ?? 0)
      return route.fulfill({
        json: {
          catalog_id: catalogId,
          catalog_revision: 1,
          items: items.slice(start, start + 25),
          counts,
          matching_filter: items.length,
          next_cursor: items.length > start + 25 ? String(start + 25) : null,
          assessment_id: url.searchParams.get('assessment_id'),
          assessment_counts: assessment.counts,
          mode: mode ?? 'all',
          limit_reasons: [],
        },
      })
    }
    return route.fulfill({ json: catalog })
  })
  return { writes, queries }
}

test('saved all/relevant views keep honest counts and issue no inference writes', async ({
  page,
}, testInfo) => {
  const { writes } = await setup(page)
  await page.goto(`/catalogs/${catalogId}?revision=1&assessment=${assessmentId}`)
  await expect(
    page.getByText('8 assessed; 3 relevant; 9992 not yet assessed; 0 failed.'),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Relevant to your project', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText('3 matching features.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'All features', exact: true }).click()
  await expect(page.getByText('10000 matching features.', { exact: false })).toBeVisible()
  await page.getByLabel('Search saved features').fill('Capability 10000')
  await expect(page.getByText('1 matching features.', { exact: false })).toBeVisible()
  const name = 'Capability 10000'
  await page.getByRole('link', { name, exact: true }).click()
  await expect(
    page.getByRole('region', { name: 'Feature detail' }).getByRole('heading', { name }),
  ).toBeFocused()
  await page.getByRole('button', { name: 'Back to features' }).click()
  await expect(page.getByRole('link', { name, exact: true })).toBeFocused()
  await page.getByLabel('Search saved features').fill('')
  await page.getByLabel('Assessment status').selectOption('unassessed')
  await expect(page.getByText('9992 matching features.', { exact: false })).toBeVisible()
  await page.getByLabel('Fit eligibility').selectOption('eligible')
  await expect(page.getByRole('heading', { name: 'No matching features' })).toBeVisible()
  await page.goBack()
  await expect(page.getByText('9992 matching features.', { exact: false })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Assessment status')).toHaveValue('unassessed')
  expect(writes).toHaveLength(0)
  expect(await page.locator('article').count()).toBeLessThanOrEqual(25)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/f5-${testInfo.project.name}.png`,
    fullPage: true,
  })
})

test('requesting relevance and editing a goal are explicit new jobs', async ({ page }) => {
  const { writes } = await setup(page)
  await page.goto(`/catalogs/${catalogId}?revision=1`)
  await expect(
    page.getByRole('button', { name: 'Relevant to your project', exact: true }),
  ).toBeDisabled()
  await page.getByLabel('Your goal').fill('Remove duplicate sequences')
  expect(writes).toHaveLength(0)
  await page.getByRole('button', { name: 'Assess relevance', exact: true }).click()
  await expect(page).toHaveURL(/assessment=f{32}/)
  await page.getByText('Assess more or change context', { exact: true }).click()
  await page.getByLabel('Your goal').fill('A different feature goal')
  await expect(page.getByText('Edited context.', { exact: false })).toBeVisible()
  await expect(page.locator('p').filter({ hasText: 'Saved goal:' })).toContainText(
    'Remove duplicate sequences',
  )
  await page.getByRole('button', { name: 'Assess relevance', exact: true }).click()
  await expect.poll(() => writes.length).toBe(2)
  expect(writes[1].body).toMatchObject({
    goal: 'A different feature goal',
    target_project: null,
    base_assessment: null,
  })
})

test('target comparison works without native tests and saves a readable expansion idea', async ({
  page,
}, info) => {
  const { writes } = await setup(page)
  const featureId = entries[0].feature_id
  const targetRef = { ...fixtureRun.references![0].ref, repo_id: 'target', path: 'src/sequence.py' }
  const saved = {
    ...assessment,
    id: 'f'.repeat(32),
    target_project: 'b'.repeat(32),
    target_origin: 'https://github.com/team/target-tools',
    target_sha: 'c'.repeat(40),
    target_root: '',
    expansion_top3: [
      {
        feature_id: featureId,
        name: 'Optional deduplication',
        benefit: 'Process repeated inputs only once.',
      },
    ],
  }
  await page.route('**/api/projects', (route) =>
    route.fulfill({
      json: {
        id: saved.target_project,
        requested: { role: 'target', project_root: '' },
        snapshot: { ...fixtureRun.target },
        support: {
          name: 'target-tools',
          capability: 'evidence_analysis',
          python_files: 2,
          execution_supported: false,
          adaptation_reason: 'Native tests unavailable.',
        },
        root_choices: [''],
      },
    }),
  )
  await page.route(`**/api/relevance-assessments/${saved.id}`, (route) =>
    route.fulfill({ json: saved }),
  )
  await page.route('**/api/relevance-assessments/*/features/*', (route) =>
    route.fulfill({
      json: {
        feature_id: featureId,
        relevance_status: 'relevant',
        target_status: 'potential_gap',
        fit_status: 'ineligible',
        reason: 'Optional deduplication can avoid repeated work.',
        expansion: {
          source_capability: 'Ordered deduplication.',
          existing_target_behavior: 'Groups the input without removing duplicates.',
          proposed_extension: 'Offer optional deduplication before grouping.',
          benefit: 'Process repeated inputs only once.',
          integration_points: [
            { target_ref: targetRef, change: 'Normalize input before grouping.' },
          ],
          constraints: ['Preserve grouping defaults.'],
          uncertainties: ['Only a bounded portion of the target was inspected.'],
          source_refs: [fixtureRun.references![0].ref],
          target_refs: [targetRef],
        },
        evidence: [
          { ref: targetRef, text: 'def chunks(items): return [items]', sha256: 'd'.repeat(64) },
        ],
        gates: [],
        rejections: [],
        judgment: null,
      },
    }),
  )
  await page.goto(`/catalogs/${catalogId}?revision=1`)
  await page.getByLabel('Your goal').fill(saved.goal)
  await page.getByLabel('Include a target project').check()
  await page.getByLabel('Target public repository').fill(saved.target_origin!)
  await page.getByLabel('Target commit SHA').fill(saved.target_sha!)
  await page.getByRole('button', { name: 'Inspect target project', exact: true }).click()
  await page.getByText('Test and implementation support', { exact: true }).click()
  await expect(page.getByText('Native tests unsupported.', { exact: false })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Find expansion ideas', exact: true }),
  ).toBeEnabled()
  expect(writes).toHaveLength(0)
  await page.getByRole('button', { name: 'Find expansion ideas', exact: true }).click()
  await expect(page).toHaveURL(/assessment=f{32}/)
  expect(writes).toHaveLength(1)
  expect(writes[0].body).toMatchObject({ target_project: saved.target_project, goal: saved.goal })
  expect(writes[0].body).not.toHaveProperty('run_baseline')
  await page.getByRole('region', { name: 'Expansion ideas', exact: true }).getByRole('link').click()
  const idea = page.getByRole('region', { name: 'Expansion recommendation', exact: true })
  await expect(idea.getByText('Process repeated inputs only once.')).toBeVisible()
  await expect(idea.getByText('Groups the input without removing duplicates.')).toBeVisible()
  await expect(idea.getByText('Normalize input before grouping.')).toBeVisible()
  await expect(idea.getByText('Only a bounded portion of the target was inspected.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Verify adaptation', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({
    path: `../data/frontend-qa/semantic-${info.project.name}-expansion.png`,
    fullPage: true,
  })
  await page.reload()
  await expect(idea.getByText('Process repeated inputs only once.')).toBeVisible()
  expect(writes).toHaveLength(1)
})

test('a selected assessment shows failed gates and supports keyboard return at tablet width', async ({
  page,
}) => {
  const { writes } = await setup(page)
  await page.setViewportSize({ width: 768, height: 1100 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(`/catalogs/${catalogId}?revision=1&assessment=${assessmentId}`)
  await page.getByRole('link', { name: 'Capability 00001', exact: true }).click()
  await page.getByText('Fit gates and rejection reasons').click()
  await expect(page.getByText('acceptance readiness: unknown')).toBeVisible()
  await page
    .getByRole('region', { name: 'Feature detail' })
    .getByRole('heading', { name: 'Capability 00001' })
    .focus()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('link', { name: 'Capability 00001', exact: true })).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(writes).toHaveLength(0)
})

test('adaptation starts explicitly from a saved feature and retries with the same key', async ({
  page,
}) => {
  await setup(page)
  const requests: { body: Record<string, unknown>; key: string }[] = []
  await page.route('**/api/relevance-assessments/*/features/*', (route) =>
    route.fulfill({
      json: {
        feature_id: entries[0].feature_id,
        relevance_status: 'relevant',
        relevance_score: 5,
        reason: 'A candidate for independent acceptance.',
        target_status: 'potential_gap',
        fit_status: 'ineligible',
        fit: fixtureRun.fit!.candidates[0],
        gates: [],
        rejections: [],
        judgment: null,
        failure_code: null,
        evidence: [],
        evidence_limit_reached: false,
      },
    }),
  )
  await page.route('**/api/runs', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    requests.push({
      body: route.request().postDataJSON(),
      key: route.request().headers()['idempotency-key'],
    })
    return requests.length === 1
      ? route.fulfill({ status: 503, json: { detail: 'Temporary queue outage.' } })
      : route.fulfill({ json: fixtureRun })
  })
  await page.goto(`/catalogs/${catalogId}?revision=1&assessment=${assessmentId}`)
  await page.getByRole('link', { name: 'Capability 00001', exact: true }).click()
  expect(requests).toHaveLength(0)
  await page.getByText('Implementation and verification', { exact: true }).click()
  await page.getByRole('button', { name: 'Verify adaptation', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Temporary queue outage.' })).toBeVisible()
  await page.getByRole('button', { name: 'Verify adaptation', exact: true }).click()
  await expect(page).toHaveURL(/runs\/fixture-run\/overview/)
  expect(requests).toHaveLength(2)
  expect(requests[0].key).toBe(requests[1].key)
  expect(requests[1].body).toMatchObject({
    kind: 'verified_adaptation',
    assessment_id: assessmentId,
    feature_id: entries[0].feature_id,
  })
})

test('an older search cannot overwrite the current saved query', async ({ page }) => {
  const { writes } = await setup(page)
  let started = false
  let release!: () => void
  let complete!: () => void
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  const finished = new Promise<void>((resolve) => {
    complete = resolve
  })
  await page.route('**/api/catalogs/*/features?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('q') !== 'slow') return route.fallback()
    started = true
    await held
    await route
      .fulfill({
        json: {
          catalog_id: catalogId,
          catalog_revision: 1,
          items: [],
          counts,
          matching_filter: 0,
          next_cursor: null,
        },
      })
      .catch(() => {})
    complete()
  })
  await page.goto(`/catalogs/${catalogId}?revision=1`)
  await page.getByLabel('Search saved features').fill('slow')
  await expect.poll(() => started).toBe(true)
  await page.getByLabel('Search saved features').fill('Capability 10000')
  await expect(page.getByText('1 matching features.', { exact: false })).toBeVisible()
  release()
  await finished
  await expect(page.getByRole('link', { name: 'Capability 10000', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No matching features' })).toHaveCount(0)
  expect(writes).toHaveLength(0)
})
