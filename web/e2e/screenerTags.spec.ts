import { expect, test } from '@playwright/test'

test('saved screener tags compose without crowding the result list', async ({ page }, info) => {
  const run = { run_id: 'tag-test', strategy_id: 'major-descending-breakout', strategy_version: 'v1',
    as_of_date: '2026-09-11', parameters: {}, status: 'succeeded', candidate_count: 20,
    universe_count: 20, scanned_count: 20 }
  const candidates = Array.from({ length: 20 }, (_, i) => ({
    rank: i + 1, symbol: `000${100 + i}.SZ`, name: `测试标的${i + 1}`, exchange: 'SZ', kind: 'stock',
    state: i % 2 ? 'broken-out' : 'breakout-retest', score: 80,
    analysis_run_id: 'tag-analysis', line_item_id: 'line-1', line_code: 'MDL-1',
    evidence: { period: '1y', as_of_date: run.as_of_date },
    recognition: { available: true, source_date: '2026-09-07', source_run_id: 'weekly-1', tags: i < 6 ? ['historical'] : [] },
  }))
  await page.route('**/api/screener/**', route => route.fulfill({ json: {
    items: route.request().url().endsWith('/candidates') ? candidates : [run],
  } }))
  await page.route('**/api/analysis/runs/tag-analysis', route => route.fulfill({ json: null }))
  await page.goto('/')
  await page.getByRole('button', { name: '选股器', exact: true }).click()
  await page.getByRole('button', { name: /历史辨识度/ }).click()
  await expect(page.getByText(/显示 6\/20/)).toBeVisible()
  await page.getByRole('button', { name: '快速过滤：已突破' }).click()
  await expect(page.getByText(/显示 3\/20/)).toBeVisible()
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 900 })
    const list = page.locator('.screener-results > .screener-scroll')
    const tags = page.getByRole('group', { name: '辨识度标签过滤' })
    const a = (await tags.boundingBox())!
    const b = (await list.boundingBox())!
    expect(b.y).toBeGreaterThan(a.y + a.height)
    expect(b.height).toBeGreaterThan(200)
    await page.screenshot({ path: info.outputPath(`screener-tags-${width}.png`) })
  }
})
