import { expect, test } from '@playwright/test'
import { analysisLayers } from '../src/AnalysisOverlayToggle'

test('main chart targets require checking the shared analysis toggle', async ({ page }, info) => {
  const bars = Array.from({ length: 120 }, (_, i) => ({
    trade_date: new Date(Date.UTC(2026, 4, 1 + i)).toISOString().slice(0, 10),
    open: 11.9 + Math.sin(i / 8), high: 12.4 + Math.sin(i / 8),
    low: 11.5 + Math.sin(i / 8), close: 12 + Math.sin(i / 8), volume: 100 + i,
    source: 'synthetic-acceptance',
  }))
  const run = { run_id: 'main-visibility', as_of_date: bars.at(-1)!.trade_date,
    completion_state: 'complete', stale: false, stale_reasons: [], warnings: [], items: [{
      item_id: 'scenario', item_type: 'scenario', payload: {
        kind: 'structural-trade-scenario', primary: true, rank: 1, direction: 'long',
        state: 'waiting-trigger', entry_price: 12, invalidation_price: 11.5,
        has_trade_space: true, selected_target_label: 'T1',
        targets: [{ label: 'T1', price: 13, basis: 'key-level', stressed_risk_reward_ratio: 2 }],
      },
    }, { item_id: 'line', item_type: 'line', payload: {
      kind: 'support', horizon: 'long', first_pivot_date: bars[60].trade_date,
      second_pivot_date: bars[100].trade_date, first_price: 11.5, second_price: 12,
    } }, { item_id: 'pivot', item_type: 'anchor', payload: {
      kind: 'low', pivot_date: bars[100].trade_date, price: 11.5,
    } }] }
  await page.route('**/api/instruments/*/daily-bars**', route => route.fulfill({ json: { items: bars, instrument_kind: 'stock' } }))
  await page.route('**/api/analysis/trend/**', route => route.fulfill({ json: route.request().url().includes('/runs?')
    ? { items: [] } : { official: run, effective: run, preview: null, preview_expired: false } }))
  await page.goto('/')
  await page.getByRole('button', { name: '启用趋势交易体系', exact: true }).first().click()
  await page.getByRole('button', { name: '打开形态分析结果' }).first().click()
  const checkbox = page.getByRole('checkbox', { name: '显示趋势目标与盈亏比' })
  await expect(checkbox).not.toBeChecked()
  for (const label of Object.values(analysisLayers)) {
    await expect(page.getByRole('checkbox', { name: `显示${label}`, exact: true })).not.toBeChecked()
  }
  await expect(page.locator('.generated-trend-line')).toHaveCount(0)
  await expect(page.locator('.generated-pivot')).toHaveCount(0)
  await page.getByRole('checkbox', { name: '显示长期关键趋势线' }).check()
  await expect(page.locator('.generated-trend-line')).toHaveCount(1)
  await expect(page.locator('.generated-pivot')).toHaveCount(0)
  await page.getByRole('checkbox', { name: '显示拐点标记', exact: true }).check()
  await expect(page.locator('.generated-pivot')).toHaveCount(1)
  await page.getByRole('checkbox', { name: '显示长期关键趋势线' }).uncheck()
  await page.getByRole('checkbox', { name: '显示拐点标记', exact: true }).uncheck()
  await expect(page.locator('.generated-risk-reward')).toHaveCount(0)
  await checkbox.check()
  await expect(page.locator('.generated-risk-reward')).toBeVisible()
  await page.screenshot({ path: info.outputPath('main-targets-checked.png') })
  await checkbox.uncheck()
  await expect(page.locator('.generated-risk-reward')).toHaveCount(0)
  await page.screenshot({ path: info.outputPath('main-targets-hidden.png') })
  await checkbox.check()
  await page.reload()
  await page.getByRole('button', { name: '打开形态分析结果' }).first().click()
  await expect(page.getByRole('checkbox', { name: '显示趋势目标与盈亏比' })).not.toBeChecked()
  await expect(page.locator('.generated-risk-reward')).toHaveCount(0)
})
