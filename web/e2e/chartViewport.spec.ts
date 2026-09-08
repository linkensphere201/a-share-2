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

async function mainPaneCandleOccupancy(page: import('@playwright/test').Page) {
  return page.locator('.chart-stage').first().evaluate(stage => {
    const colors = [[239, 83, 80], [38, 162, 105], [233, 150, 147], [112, 190, 154]]
    const stageTop = stage.getBoundingClientRect().top
    const samples: number[] = []
    let paneHeight = 0
    for (const canvas of stage.querySelectorAll('canvas')) {
      const bounds = canvas.getBoundingClientRect()
      if (Math.abs(bounds.top - stageTop) > 2 || bounds.height <= 0) continue
      const context = canvas.getContext('2d')
      if (!context) continue
      paneHeight = Math.max(paneHeight, bounds.height)
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      const ratio = canvas.height / bounds.height
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (pixels[offset + 3] < 160) continue
        if (!colors.some(([r, g, b]) => Math.abs(pixels[offset] - r) <= 10
          && Math.abs(pixels[offset + 1] - g) <= 10
          && Math.abs(pixels[offset + 2] - b) <= 10)) continue
        samples.push(Math.floor(offset / 4 / canvas.width) / ratio)
      }
    }
    if (!samples.length || paneHeight <= 0) throw new Error('No main-pane candle pixels found')
    return (Math.max(...samples) - Math.min(...samples)) / paneHeight
  })
}

test('vertical drag is dampened and capped without flattening prices', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173')
  const stage = page.locator('.chart-stage').first()
  await expect(stage).toBeVisible()
  await expect(stage.locator('.chart-state')).toHaveCount(0, { timeout: 15_000 })
  await page.waitForTimeout(500)
  expect(await mainPaneCandleOccupancy(page)).toBeGreaterThan(0.55)
  const before = await candleCentroid(page)
  const box = await stage.boundingBox()
  if (!box) throw new Error('Chart has no bounds')
  const x = box.x + box.width * 0.56
  const y = box.y + box.height * 0.32
  for (let index = 0; index < 5; index += 1) {
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 30, y + 520, { steps: 20 })
    await page.mouse.up()
  }
  await page.waitForTimeout(350)
  const after = await candleCentroid(page)
  expect(Math.abs(after.x - before.x)).toBeLessThan(8)
  expect(after.y - before.y).toBeGreaterThan(25)
  expect(after.y - before.y).toBeLessThan(100)
  expect(await mainPaneCandleOccupancy(page)).toBeGreaterThan(0.6)
})

test('dominant horizontal drag refits prices for the newly visible history', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173')
  const stage = page.locator('.chart-stage').first()
  await expect(stage.locator('.chart-state')).toHaveCount(0, { timeout: 15_000 })
  const box = await stage.boundingBox()
  if (!box) throw new Error('Chart has no bounds')
  const x = box.x + box.width * 0.56
  const y = box.y + box.height * 0.32
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 520, y, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(500)
  expect(await mainPaneCandleOccupancy(page)).toBeGreaterThan(0.6)
})

test('wheel zoom-out stops at the complete data span instead of scaling empty time', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173')
  const stage = page.locator('.chart-stage').first()
  await expect(stage.locator('.chart-state')).toHaveCount(0, { timeout: 15_000 })
  const box = await stage.boundingBox()
  if (!box) throw new Error('Chart has no bounds')
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3)
  for (let index = 0; index < 12; index += 1) {
    await page.mouse.wheel(0, 120)
    await page.waitForTimeout(80)
  }
  await page.waitForTimeout(500)
  const atLimit = await mainPaneCandleOccupancy(page)
  for (let index = 0; index < 12; index += 1) {
    await page.mouse.wheel(0, 120)
    await page.waitForTimeout(80)
  }
  await page.waitForTimeout(500)
  const beyondLimit = await mainPaneCandleOccupancy(page)
  expect(atLimit).toBeGreaterThan(0.6)
  expect(beyondLimit).toBeGreaterThan(0.6)
  expect(Math.abs(beyondLimit - atLimit)).toBeLessThan(0.03)
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
