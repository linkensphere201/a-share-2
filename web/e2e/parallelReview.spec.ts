import { expect, test } from '@playwright/test'

test('daily modules show both analytical leaders in the real workspace', async ({ page }, info) => {
  const definition = { signal_id: 'daily-market-board-review', name: '每日复盘', cadence: 'daily',
    profiles: ['market', 'attention'], definition_version: 'v1', algorithm_version: 'v1', manual_only: true }
  const run = { run_id: 'parallel-test', signal_id: definition.signal_id, cadence: 'daily',
    effective_date: '2026-09-14', revision: 1, status: 'succeeded', phase: 'completed',
    work_total: 1, work_done: 1, item_count: 0, added_count: 0, retained_count: 0,
    removed_count: 0, summary: {} }
  const scores = ['trend-breakout', 'mean-reversion'].map((system, index) => ({
    system_id: system, entity_scope: 'board', entity_key: `BK000${index}.DC`, symbol: `BK000${index}.DC`,
    name: index ? '回归观察板块' : '趋势观察板块', grade: 'A', total_score: 82 - index, rank: 1,
    eligible: true, setup_family: index ? 'directional-pullback' : 'breakout',
    summary: index ? '偏离收敛，缩量企稳。' : '关键边界突破，量价配合。',
    hard_events: [], history: [], components: {}, penalties: [], disqualifiers: [],
  }))
  await page.route('**/api/signals/**', route => {
    const url = route.request().url()
    const data = url.endsWith('/definitions') ? { items: [definition] }
      : url.includes('/runs?') ? { items: [run] }
      : url.endsWith('/scores') ? { items: scores }
      : { items: [], total: 0 }
    return route.fulfill({ json: data })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '信号复盘', exact: true }).click()
  await expect(page.getByLabel('趋势体系前排')).toBeVisible()
  await expect(page.getByLabel('均值回归前排')).toBeVisible()
  await expect(page.getByRole('button', { name: '均值回归', exact: true })).toHaveCount(0)
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 900 })
    const a = await page.getByLabel('趋势体系前排').boundingBox()
    const b = await page.getByLabel('均值回归前排').boundingBox()
    expect(a!.x + a!.width).toBeLessThanOrEqual(b!.x + 1)
    expect(a!.height).toBeGreaterThan(100)
    await page.screenshot({ path: info.outputPath(`parallel-${width}.png`) })
  }
  await page.getByRole('button', { name: /机会评分/ }).click()
  await expect(page.getByLabel('均值回归前排').getByText(/回归观察板块/)).toBeVisible()
})
