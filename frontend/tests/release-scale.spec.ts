import { test, expect } from '@playwright/test'
import type { CatalogPage } from '../src/api/client'

declare global {
  interface Window {
    releaseLongTasks: number[]
  }
}

test('production build browses real API scale fixtures with bounded pages and keyboard focus', async ({
  page,
  request,
}, testInfo) => {
  test.skip(!process.env.GRAFT_RELEASE_ROOT, 'Disposable F9 validation server only.')
  test.setTimeout(90_000)
  const errors: string[] = []
  const writes: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (call) => {
    if (call.method() !== 'GET') writes.push(call.method() + ' ' + new URL(call.url()).pathname)
  })
  // Chrome's Long Tasks API measures the interaction window separately from startup.
  await page.addInitScript(() => {
    const durations: number[] = []
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) durations.push(entry.duration)
    })
    observer.observe({ type: 'longtask' })
    Object.assign(window, { releaseLongTasks: durations })
  })
  const measurements = []
  const cdp = await page.context().newCDPSession(page)
  for (const size of [100, 1000, 5000, 10000]) {
    const id = size.toString(16).padStart(32, '0')
    const assessment = (size + 10000).toString(16).padStart(32, '0')
    const path = `/api/catalogs/${id}/features?revision=1&assessment_id=${assessment}&mode=all`
    const response = await request.get(path)
    expect(response.ok()).toBeTruthy()
    const body = await response.body()
    expect(body.byteLength).toBeLessThanOrEqual(100 * 1024)
    const first = (await response.json()) as CatalogPage
    expect(first.matching_filter).toBe(size)
    expect(first.items).toHaveLength(25)
    expect(first.items.every((item) => !('card' in item) && !('gates' in item))).toBe(true)
    const httpSamples = []
    for (let sample = 0; sample < 30; sample++) {
      const started = performance.now()
      const measured = await request.get(path)
      expect(measured.ok()).toBe(true)
      await measured.body()
      httpSamples.push(performance.now() - started)
    }
    const httpP95 = httpSamples.sort((a, b) => a - b)[Math.ceil(httpSamples.length * 0.95) - 1]
    expect(
      httpP95,
      'Warm loopback HTTP/client latency exceeds the 300 ms budget',
    ).toBeLessThanOrEqual(300)
    await page.goto(`/catalogs/${id}?revision=1&assessment=${assessment}&mode=all`)
    await expect(page.getByRole('link', { name: 'Capability 00001', exact: true })).toBeVisible()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const before = await page.locator('*').count()
    await cdp.send('HeapProfiler.collectGarbage')
    const heapBefore = await cdp.send('Runtime.getHeapUsage')
    const nodeCounts = []
    const seen = new Set<string>()
    for (let index = 0; index < 10; index++) {
      const names = await page.locator('article h2').allTextContents()
      expect(names).toHaveLength(25)
      for (const name of names) {
        expect(seen.has(name)).toBe(false)
        seen.add(name)
      }
      nodeCounts.push(await page.locator('*').count())
      if (seen.size === size || index === 9) break
      const next = page.waitForResponse((r) => r.url().includes('/features?') && r.ok())
      await page.getByRole('button', { name: 'Next page', exact: true }).click()
      await next
      await expect(page.getByRole('link', { name: names[0], exact: true })).toHaveCount(0)
    }
    expect(Math.max(...nodeCounts)).toBeLessThanOrEqual(before + 10)
    await cdp.send('HeapProfiler.collectGarbage')
    const heapAfter = await cdp.send('Runtime.getHeapUsage')
    expect(heapAfter.usedSize - heapBefore.usedSize).toBeLessThanOrEqual(8 * 1024 * 1024)
    const search = page.getByLabel('Search saved features')
    await search.focus()
    await page.evaluate(() => {
      window.releaseLongTasks.length = 0
    })
    await search.pressSequentially(`Capability ${String(size).padStart(5, '0')}`)
    await expect(search).toHaveValue(`Capability ${String(size).padStart(5, '0')}`)
    const link = page.getByRole('link', {
      name: `Capability ${String(size).padStart(5, '0')}`,
      exact: true,
    })
    await expect(link).toBeVisible()
    await expect(page.locator('article')).toHaveCount(1)
    await link.focus()
    await page.keyboard.press('Enter')
    await expect(
      page.getByRole('region', { name: 'Feature detail' }).getByRole('heading', { level: 2 }),
    ).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(link).toBeFocused()
    // Deliver queued observer entries before reading the bounded interaction window.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    )
    const tasks = await page.evaluate(() => window.releaseLongTasks)
    expect(tasks, 'Search/select long tasks above the 50 ms release budget').toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await search.fill('')
    await page.getByRole('button', { name: 'Relevant to your project', exact: true }).click()
    await expect(page.locator('article')).toHaveCount(3)
    await page.reload()
    await expect(
      page.getByRole('button', { name: 'Relevant to your project', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('article')).toHaveCount(3)
    measurements.push({
      rows: size,
      page_bytes: body.byteLength,
      loopback_http_samples: httpSamples.length,
      loopback_http_p95_ms: httpP95,
      browsed_entries: seen.size,
      max_dom_nodes: Math.max(...nodeCounts),
      heap_before_bytes: heapBefore.usedSize,
      heap_after_bytes: heapAfter.usedSize,
      search_select_long_tasks_ms: tasks,
    })
  }
  expect(errors).toEqual([])
  expect(writes).toEqual([])
  expect((await request.get('/health')).ok()).toBe(true)
  expect((await request.get('/api/capabilities')).ok()).toBe(true)
  await testInfo.attach('scale-measurements', {
    body: JSON.stringify(
      {
        browser: page.context().browser()?.version(),
        viewport: testInfo.project.use.viewport,
        measurements,
      },
      null,
      2,
    ),
    contentType: 'application/json',
  })
  await page.screenshot({ path: testInfo.outputPath('scale-final.png'), fullPage: true })
})
