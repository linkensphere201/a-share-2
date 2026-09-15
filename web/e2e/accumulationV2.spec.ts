import { expect, test } from '@playwright/test'

test('accumulation V2 evidence and range remain readable at desktop and mobile widths', async ({ page }, info) => {
  const bars = Array.from({ length: 100 }, (_, i) => {
    const close = i < 70 ? 16 - i * .07 : 11.3 + .04 * (i % 3)
    return { trade_date: new Date(Date.UTC(2026, 4, 1 + i)).toISOString().slice(0, 10),
      open: close * .995, high: close * 1.01, low: close * .99, close,
      volume: i % 2 ? 100 : 150, source: 'synthetic-acceptance' }
  })
  const run = { run_id: 'acc-v2', strategy_id: 'volume-accumulation-20d', strategy_version: 'v5',
    as_of_date: bars.at(-1)!.trade_date, parameters: {}, status: 'succeeded', candidate_count: 1,
    universe_count: 1, scanned_count: 1 }
  const evidence = { as_of_date: run.as_of_date, stage: 'pending-digestion', platform_sessions: 14,
    decline_return_percent: -25, ma_divergence_percent: 5, bottom_lift_percent: 6,
    platform_range_percent: 4, platform_return_percent: 1, small_body_sessions: 12,
    platform_volume_ratio: 1.4, average_up_down_volume_ratio: 1.5,
    robust_up_down_volume_ratio: 1.3, limit_up_count: 1, decline_slowing: true,
    invalidation_price: 10.8, score_components: { decline: 18, lift: 19, platform: 20, demand: 25 },
    missing_evidence: ['turnover-unavailable'] }
  const candidate = { rank: 1, symbol: '000001.SZ', name: '合成验收样本', exchange: 'SZ', kind: 'stock',
    state: 'accumulating', score: 82, analysis_run_id: 'acc-analysis', line_item_id: '',
    line_code: 'VOL-ACC-20D', evidence }
  await page.route('**/api/screener/**', route => route.fulfill({ json: {
    items: route.request().url().endsWith('/candidates') ? [candidate] : [run],
  } }))
  await page.route('**/api/instrument-board-memberships?**', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/instruments/000001.SZ/daily-bars**', route => route.fulfill({
    json: { items: bars, instrument_kind: 'stock' },
  }))
  await page.route('**/api/analysis/runs/acc-analysis', route => route.fulfill({ json: {
    run_id: 'acc-analysis', as_of_date: run.as_of_date, completion_state: 'complete',
    stale: false, stale_reasons: [], warnings: [], items: [{
      item_id: 'acc-zone', item_type: 'zone', payload: { ...evidence,
        kind: 'accumulation-range', lower: 11.1, upper: 11.6, score: 82,
        start_date: bars.at(-14)!.trade_date, end_date: bars.at(-1)!.trade_date },
    }],
  } }))
  await page.goto('/')
  await page.getByRole('button', { name: '选股器', exact: true }).click()
  const footer = page.locator('.screener-evidence')
  await expect(footer).toContainText('待涨停消化')
  await expect(footer).toContainText('12/14')
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await footer.scrollIntoViewIfNeeded()
    await expect(page.locator('.generated-price-zone.accumulation-range')).toBeVisible()
    const overflow = await footer.locator('span').evaluateAll(elements => elements.some(
      element => element.scrollWidth > element.clientWidth + 1,
    ))
    expect(overflow).toBe(false)
    const pane = (await page.locator('.screener-chart-pane').boundingBox())!
    expect(pane.x + pane.width).toBeLessThanOrEqual(width + 1)
    await page.screenshot({ path: info.outputPath('accumulation-' + width + '.png') })
  }
})
