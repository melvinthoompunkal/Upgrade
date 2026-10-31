import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { mockApi } from './fixtures'
import type { ProjectSelection, Catalog } from '../src/api/client'

test('parserless source inspection enables discovery without a manifest or runner', async ({
  page,
}) => {
  await mockApi(page)
  const project: ProjectSelection = {
    id: 'a'.repeat(32),
    requested: {
      url: 'https://github.com/corpus/text',
      role: 'source',
      commit_sha: 'b'.repeat(40),
      project_root: 'web',
      analysis_profile: 'repository-text-v1',
    },
    snapshot: {
      repo_id: 'source',
      origin: 'https://github.com/corpus/text',
      commit_sha: 'b'.repeat(40),
      inventory_artifact_id: 'source-inventory.json',
      license_refs: [],
    },
    inventory_policy: 'repository-text-inventory-v1',
    inventory_sha256: 'c'.repeat(64),
    root_choices: ['web', 'web/src'],
    created_at: '2026-10-30T22:00:00Z',
    private: false,
    support: {
      schema_version: 'project-support-v2',
      profile_version: 'repository-text-v1',
      repo_id: 'source',
      commit_sha: 'b'.repeat(40),
      project_root: 'web',
      name: 'Text corpus',
      capability: 'evidence_analysis',
      analysis_available: true,
      detected_languages: { TypeScript: 2, Rust: 1 },
      readable_files: 5,
      readable_source_files: 3,
      python_files: 0,
      manifest_sha256: null,
      manifest_paths: [],
      structural_coverage: 'text_only',
      execution_supported: false,
      adaptation_supported: false,
      execution_reasons: ['Text analysis has no execution profile.'],
      adaptation_reason: 'Generic cards have no reviewed adaptation profile.',
      notice_policy: 'analysis-only-v1',
      notice_paths: [],
      inventory_partial: false,
      inventory_limits: [],
    },
  }
  let submitted: Record<string, unknown> | undefined
  await page.route('**/api/projects', (route) => {
    submitted = route.request().postDataJSON()
    return route.fulfill({ json: project })
  })
  await page.goto('/')
  await page.getByLabel('Analysis profile').selectOption('repository-text-v1')
  await page.getByLabel('Source public repository').fill(project.requested.url)
  await page.getByRole('button', { name: 'Inspect source project' }).click()
  await expect(page.getByText('Ready for feature discovery', { exact: true })).toBeVisible()
  await expect(page.getByText(/5 readable files; 3 source files/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Discover features', exact: true })).toBeEnabled()
  expect(submitted?.analysis_profile).toBe('repository-text-v1')
  await page.getByText('Test and implementation support', { exact: true }).click()
  await expect(page.getByText(/Native tests unsupported/)).toBeVisible()
  await expect(page.getByText('Generic cards have no reviewed adaptation profile.')).toBeVisible()
  await expect(page.getByText('Browse only', { exact: true })).toHaveCount(0)
})

test('actual saved v2 catalog reopens and downloads with zero inference', async ({
  page,
  request,
}, testInfo) => {
  test.skip(!process.env.FE_BROWSER_CONTEXT, 'Requires a saved-data context and read-only server.')
  const context = JSON.parse(await readFile(process.env.FE_BROWSER_CONTEXT!, 'utf-8')) as {
    catalog_id: string
    revision: number
    feature_id: string
    feature_name?: string
    run_id: string
  }
  const featureName = context.feature_name ?? 'normalization'
  const errors: string[] = []
  const writes: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (r) => {
    if (r.method() === 'POST') writes.push(r.url())
  })
  const url = `/catalogs/${context.catalog_id}?revision=${context.revision}&feature=${context.feature_id}`
  const before = await request.get(`/api/catalogs/${context.catalog_id}?revision=1`)
  const original = (await before.json()) as Catalog
  await page.goto(url)
  await expect(
    page
      .getByRole('region', { name: 'Feature detail' })
      .getByRole('heading', { name: featureName, exact: true }),
  ).toBeVisible()
  await expect(page.getByText('Code cited · Text only · Tests not run')).toBeVisible()
  await page.getByText('Exact text coverage · tests not run', { exact: true }).click()
  await expect(page.getByText(/partially read/)).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download feature JSON' }).click()
  const file = await download
  const exported = JSON.parse(await readFile((await file.path())!, 'utf-8'))
  expect(exported.schema_version).toBe('source-feature-export-v2')
  expect(exported.feature.card.execution_verification).toBe('not_run')
  expect(
    exported.feature.evidence.every(
      (e: { ref: { blob_sha256: string; excerpt_sha256: string } }) =>
        e.ref.blob_sha256 && e.ref.excerpt_sha256,
    ),
  ).toBe(true)
  await page.reload()
  await expect(
    page
      .getByRole('region', { name: 'Feature detail' })
      .getByRole('heading', { name: featureName, exact: true }),
  ).toBeVisible()
  const after = (await (
    await request.get(`/api/catalogs/${context.catalog_id}?revision=1`)
  ).json()) as Catalog
  expect(after.usage).toEqual(original.usage)
  expect(writes).toEqual([])
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: testInfo.outputPath('text-saved-result.png'), fullPage: true })
})
