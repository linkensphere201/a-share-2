import { expect, test } from '@playwright/test'

for (const strategyId of ['strong-first-pullback', 'low-base-platform-pullback']) {
test(`${strategyId} saved evidence uses opt-in shared chart zones at desktop and mobile widths`, async ({ page }, info) => {
  const bars = Array.from({ length: 100 }, (_, i) => {
    const close = i < 80 ? 10 + .02 * (i % 3) : i < 89 ? 10.5 + (i - 80) * .25 : 12.4 - (i - 89) * .09
    return { trade_date: new Date(Date.UTC(2026, 4, 1 + i)).toISOString().slice(0, 10),
      open: close - .04, high: close + .1, low: close - .12, close,
      volume: i < 80 || i >= 89 ? 100 : 240, source: 'synthetic-acceptance' }
  })
  const run = { run_id: 'first-pullback-v1', strategy_id: strategyId, strategy_version: `${strategyId}-${strategyId === 'low-base-platform-pullback' ? 'v4' : 'v1'}`,
    as_of_date: bars.at(-1)!.trade_date, parameters: {}, status: 'succeeded', candidate_count: 1,
    universe_count: 1, scanned_count: 1 }
  const evidence = { kind: 'first-pullback-range', as_of_date: run.as_of_date,
    ...(strategyId === 'low-base-platform-pullback' ? {
      shape_maturity: 'platform-retest', platform_shape: { start_date: bars[89].trade_date,
        end_date: bars[94].trade_date, sessions: 6, stable: true, mature: true,
        close_drift_percent: -.7, small_body_fraction: .83, volume_quality: 'persistent-bullish-volume' },
    } : {}),
    launch_type: strategyId === 'low-base-platform-pullback' ? 'low-base-platform' : 'base-breakout',
    observation_window_sessions: 30, origin_above_context_low_percent: 8,
    platform_range_percent: 5, pullback_platform_volume_ratio: .65,
    flag_window: { start_date: bars[90].trade_date, end_date: run.as_of_date, sessions: 10, phase: 'low-base-platform' },
    stage: 'pullback-observation', screen_eligible: true, launch_date: bars[80].trade_date,
    peak_date: bars[88].trade_date, confirmation_date: null, pullback_sessions: 10,
    impulse_gain_percent: 25, pullback_depth_percent: 8, pullback_volume_ratio: .42,
    invalidation_price: 11, first_target_price: 12.6, first_risk_reward: 2,
    lower: 11.1, upper: 11.6, score: 80, start_date: bars[90].trade_date, end_date: run.as_of_date }
  const candidate = { rank: 1, symbol: '000001.SZ', name: '首踩合成验收', exchange: 'SZ', kind: 'stock',
    state: 'pullback-observation', score: 80, analysis_run_id: 'first-analysis', line_item_id: 'first-zone',
    line_code: 'FIRST-PULLBACK', evidence }
  await page.route('**/api/screener/**', route => route.fulfill({ json: {
    items: route.request().url().endsWith('/candidates') ? [candidate] : [run],
  } }))
  await page.route('**/api/instrument-board-memberships?**', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/instruments/000001.SZ/daily-bars**', route => route.fulfill({ json: { items: bars, instrument_kind: 'stock' } }))
  await page.route('**/api/analysis/runs/first-analysis', route => route.fulfill({ json: {
    run_id: 'first-analysis', as_of_date: run.as_of_date, completion_state: 'complete',
    stale: false, stale_reasons: [], warnings: [], items: [{ item_id: 'first-zone', item_type: 'zone', payload: evidence }],
  } }))
  await page.goto('/')
  await page.getByRole('button', { name: '选股器', exact: true }).click()
  await page.getByLabel('策略').selectOption(strategyId)
  await expect(page.locator('.screener-evidence')).toContainText('尚未确认')
  if (strategyId === 'low-base-platform-pullback') {
    const filters = page.getByRole('group', { name: '形态成熟度过滤' })
    await expect(filters.getByRole('button', { name: /平台缩量回踩/ })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.screener-evidence')).toContainText('独立平台')
    await expect(page.locator('.screener-evidence')).not.toContainText('参考盈亏比')
    await filters.getByRole('button', { name: /初步形成/ }).click()
    await expect(page.locator('.screener-evidence')).toHaveCount(0)
    await filters.getByRole('button', { name: /平台缩量回踩/ }).click()
  }
  await expect(page.locator('.generated-price-zone.first-pullback-range')).toHaveCount(0)
  await page.getByRole('button', { name: '打开形态分析结果' }).click()
  const checkbox = page.getByRole('checkbox', { name: '显示关键价位与形态区间', exact: true })
  await expect(checkbox).not.toBeChecked()
  await checkbox.check()
  await expect(page.locator('.generated-price-zone.first-pullback-range')).toHaveCount(1)
  await page.getByRole('button', { name: '关闭趋势分析结果说明', exact: true }).click()
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.screener-evidence').scrollIntoViewIfNeeded()
    await expect(page.locator('.generated-price-zone.first-pullback-range')).toBeVisible()
    expect(await page.locator('.screener-evidence span').evaluateAll(elements => elements.some(
      e => e.scrollWidth > e.clientWidth + 1,
    ))).toBe(false)
    const pane = (await page.locator('.screener-chart-pane').boundingBox())!
    expect(pane.x + pane.width).toBeLessThanOrEqual(width + 1)
    expect(await page.locator('.screener-chart-pane canvas').evaluateAll(canvases => canvases.some(canvas => {
      const c = canvas as HTMLCanvasElement
      const context = c.getContext('2d')
      if (!context || !c.width || !c.height) return false
      const pixels = context.getImageData(0, 0, c.width, c.height).data
      const colors = new Set<number>()
      for (let i = 0; i < pixels.length; i += 64) colors.add((pixels[i] << 16) | (pixels[i+1] << 8) | pixels[i+2])
      return colors.size > 20
    }))).toBe(true)
    await page.screenshot({ path: info.outputPath(`first-pullback-${width}.png`) })
  }
})
}
