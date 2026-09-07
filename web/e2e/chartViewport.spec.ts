import { expect, test } from '@playwright/test'

async function candleCentroid(page: import('@playwright/test').Page) {
  return page.locator('.chart-stage').first().evaluate(stage => {
    const colors = [[239, 83, 80], [38, 162, 105], [233, 150, 147], [112, 190, 154]]
    const samples: Array<{ x: number; y: number }> = []
    for (const canvas of stage.querySelectorAll('canvas')) {
      const context = canvas.getContext('2d')
      if (!context) continue
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      const ratio = canvas.height / Math.max(1, canvas.getBoundingClientRect().height)
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (pixels[offset + 3] < 160) continue
        if (!colors.some(([r, g, b]) => Math.abs(pixels[offset] - r) <= 10
          && Math.abs(pixels[offset + 1] - g) <= 10
          && Math.abs(pixels[offset + 2] - b) <= 10)) continue
        const pixel = offset / 4
        samples.push({
          x: (pixel % canvas.width) / ratio,
          y: Math.floor(pixel / canvas.width) / ratio,
        })
      }
    }
    if (!samples.length) throw new Error('No candle pixels found')
    return {
      x: samples.reduce((sum, value) => sum + value.x, 0) / samples.length,
      y: samples.reduce((sum, value) => sum + value.y, 0) / samples.length,
    }
  })
}

test('left drag pans the main chart in both dimensions', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173')
  const stage = page.locator('.chart-stage').first()
  await expect(stage).toBeVisible()
  await expect(stage.locator('.chart-state')).toHaveCount(0, { timeout: 15_000 })
  await page.waitForTimeout(500)
  const before = await candleCentroid(page)
  const box = await stage.boundingBox()
  if (!box) throw new Error('Chart has no bounds')
  const x = box.x + box.width * 0.56
  const y = box.y + box.height * 0.32
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 72, y + 54, { steps: 8 })
  await page.mouse.up()
  await page.waitForTimeout(350)
  const after = await candleCentroid(page)
  // Horizontal samples clip at the pane edge, so their centroid moves less than the pointer.
  expect(after.x - before.x).toBeGreaterThan(15)
  expect(after.x - before.x).toBeLessThan(60)
  expect(after.y - before.y).toBeGreaterThan(35)
  expect(after.y - before.y).toBeLessThan(75)
})

test('long runtime warnings cannot push the chart outside the viewport', async ({ page }) => {
  const marker = `layout-regression-${'x'.repeat(950)}`
  const response = await page.request.post('http://127.0.0.1:8001/api/runtime-events', {
    data: { level: 'WARNING', logger: 'layout-regression', message: marker },
  })
  expect(response.ok()).toBeTruthy()
  await page.goto('http://127.0.0.1:5173')
  const stage = page.locator('.chart-stage').first()
  await expect(stage.locator('.chart-state')).toHaveCount(0, { timeout: 15_000 })
  await expect(page.locator('.runtime-event-summary')).toContainText('layout-regression', {
    timeout: 5_000,
  })

  const bounds = await stage.boundingBox()
  if (!bounds) throw new Error('Chart has no bounds')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440)
  expect(bounds.x).toBeGreaterThanOrEqual(0)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(1440)
  await candleCentroid(page)
})
