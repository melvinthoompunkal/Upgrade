// Deterministic browser-test fixtures only. The application never imports this module.
import type { Page } from '@playwright/test'
import type { Capabilities, ReferenceDetail, Run, Target } from '../src/api/client'

const sha = 'a'.repeat(40)
export const reference: ReferenceDetail = {
  id: 'test-reference',
  category: 'implementation',
  excerpt_available: true,
  ref: {
    repo_id: 'source',
    commit_sha: sha,
    path: 'retry.py',
    symbol: 'retry',
    start_line: 1,
    end_line: 3,
  },
  text: 'def retry(response):\n    return response\n',
  sha256: 'b'.repeat(64),
}
export const fixtureRun: Run = {
  schema_version: '1',
  revision: 1,
  historical: false,
  id: 'fixture-run',
  kind: 'fit_select',
  title: 'Test fixture: crawler recovery',
  status: 'finished',
  cancel_requested: false,
  stage: 'complete',
  outcome: 'selected',
  created_at: '2026-10-27T16:20:00Z',
  updated_at: '2026-10-27T16:25:00Z',
  goal: 'Improve transient failure handling.',
  analyzed_feature_count: 1,
  eligible_feature_count: 1,
  source: {
    repo_id: 'source',
    origin: 'https://github.com/example/source',
    commit_sha: sha,
    inventory_artifact_id: 'source-inventory.json',
    license_refs: [],
  },
  target: {
    repo_id: 'crawler-demo',
    origin: 'registered:crawler-demo',
    commit_sha: sha,
    inventory_artifact_id: 'target-inventory.json',
    license_refs: [],
  },
  original_target_unchanged: true,
  retry_diagnostic_status: 'expected_red',
  features: [
    {
      feature_id: 'retry',
      name: 'Bounded retries',
      behavior: 'A test-only retry behavior.',
      entry_points: [reference.ref],
      implementation: [reference.ref],
      dependencies: [],
      source_tests: [],
      acceptance_behaviors: ['Temporary failures are retried.'],
      transferable_slice: 'Retry inside the target fetch boundary.',
      excluded_behaviors: ['Backoff'],
      license_spdx: 'BSD-3-Clause',
      license_refs: [],
      evidence_status: 'supported',
    },
  ],
  fit: {
    rubric_version: 'fit-v1',
    selected_feature_id: 'retry',
    selection_reason: 'The test candidate passes the recorded gates.',
    top3: ['retry'],
    rejected: [],
    candidates: [
      {
        feature_id: 'retry',
        total: 83,
        eligible: true,
        criteria: Object.fromEntries(
          ['gap_fit', 'isolation', 'dependency_cost', 'stack_compatibility', 'testability'].map(
            (name) => [
              name,
              { rating: 4, reason: `Fixture ${name} reasoning.`, evidence: [reference.ref] },
            ],
          ),
        ) as NonNullable<Run['fit']>['candidates'][number]['criteria'],
        gates: [
          { name: 'passing_baseline', passed: true, reason: 'Captured baseline passed.' },
          { name: 'target_gap', passed: true, reason: 'Missing behavior observed.' },
        ],
      },
    ],
  },
  references: [reference],
  artifacts: [
    {
      id: 'test-report',
      label: 'report.json',
      kind: 'report',
      size: 2048,
      sha256: 'c'.repeat(64),
      available: true,
    },
  ],
  coverage: {
    examined_paths: ['retry.py'],
    excluded_paths: [],
    unexamined_areas: ['other.py'],
    limits_reached: ['Bounded reads.'],
  },
  tests: [
    {
      suite: 'baseline',
      status: 'passed',
      elapsed_ms: 241,
      image_id: 'sha256:' + 'd'.repeat(64),
      diagnostic: '',
      expected_diagnostic: false,
      cases: [{ name: 'test_fetch', outcome: 'passed', message: '', detail: '' }],
    },
    {
      suite: 'acceptance',
      status: 'failed',
      elapsed_ms: 317,
      image_id: 'sha256:' + 'd'.repeat(64),
      diagnostic: '',
      expected_diagnostic: true,
      cases: [
        {
          name: 'test_transient_failure',
          outcome: 'failed',
          message: 'AssertionError: retry behavior missing',
          detail: 'Expected three attempts, observed one.',
        },
        { name: 'test_success_once', outcome: 'passed', message: '', detail: '' },
      ],
    },
  ],
  usage: { requests: 7, input_tokens: 18423, output_tokens: 2957 },
  warnings: [],
}
export const capabilities: Capabilities = {
  schema_version: '1',
  scope: 'One bounded retry feature; analysis only.',
  kinds: ['fit_select', 'source_trace', 'meta_smoke', 'pinned_baseline', 'local_baseline'],
  model_configured: true,
  model_id: 'muse-spark-1.3',
  checked_at: '2026-10-28T16:00:00Z',
  image_receipt_present: true,
  worker_available: true,
  diagnostics_allowed: true,
  patch_supported: false,
}
const target: Target = {
  id: 'crawler-demo',
  name: 'Crawler demo',
  stack: 'Python 3.12 / HTTPX',
  input_semantics: 'Committed HEAD; local changes are excluded',
  checked_at: '2026-10-28T16:00:00Z',
  head: sha,
  dirty: true,
}

export async function mockApi(page: Page, run: Run = structuredClone(fixtureRun)) {
  await page.route('http://127.0.0.1:5173/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown
    if (path === '/api/session')
      body = {
        mode: 'local',
        authenticated: true,
        user: null,
        csrf_token: null,
        connection: 'local',
        install_url: null,
      }
    else if (path === '/api/capabilities') body = capabilities
    else if (path === '/api/targets') body = [target]
    else if (path === '/api/runs' && route.request().method() === 'POST') body = run
    else if (path === '/api/runs') body = { items: [run], next_cursor: null }
    else if (path.endsWith('/events')) body = { items: [], next_cursor: 0 }
    else if (path.includes('/references/')) body = reference
    else if (path.endsWith('/text')) body = { text: '{"status":"selected"}', truncated: false }
    else if (path.includes('/artifacts/'))
      return route.fulfill({
        body: '{"status":"selected"}',
        contentType: 'application/octet-stream',
        headers: { 'Content-Disposition': 'attachment; filename="report.json"' },
      })
    else body = run
    await route.fulfill({ json: body })
  })
}
