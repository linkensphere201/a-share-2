import { expect, test } from '@playwright/test'

test('startup feedback appears before the server responds', async ({ page }, info) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let posts = 0
  const run = { run_id: 'startup-test', strategy_id: 'major-descending-breakout', strategy_version: 'v4',
    as_of_date: '2026-09-15', parameters: {}, status: 'running', candidate_count: 0,
    universe_count: 5000, scanned_count: 0 }
  await page.route('**/api/screener/**', async route => {
    if (route.request().method() === 'POST') {
      posts++
      await pending
      await route.fulfill({ status: 202, json: run })
    } else {
      await route.fulfill({ json: route.request().url().endsWith('/startup-test') ? run : { items: [] } })
    }
  })
  await page.goto('/')
  await page.getByRole('button', { name: '选股器', exact: true }).click()
  await expect(page.getByText('暂无历史结果')).toBeVisible()
  const start = page.getByRole('button', { name: '开始选股', exact: true })
  await start.click()
  await expect(page.getByRole('status')).toHaveText('正在创建选股任务大斜边突破')
  await expect(start).toBeDisabled()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(page.getByRole('status')).toBeVisible()
    await page.screenshot({ path: info.outputPath('startup-' + width + '.png') })
  }
  expect(posts).toBe(1)
  release()
  await expect(page.getByRole('status')).toHaveCount(0)
  await expect(page.getByText('大斜边突破 · 0/5000')).toBeVisible()
})
