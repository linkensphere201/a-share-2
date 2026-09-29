import { expect, test } from '@playwright/test'

test('manual scenario lab opens independently and records deferred exits', async ({ page }) => {
  await page.goto('/?view=trade-simulation')
  await expect(page.getByText('手工情景 · 非历史回测')).toBeVisible()
  await page.getByRole('combobox', { name: '载入合成测试情景' }).selectOption('1')
  await page.getByRole('button', { name: '运行模拟', exact: true }).click()
  await expect(page.getByRole('cell', { name: '延迟成交', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '卖出成交', exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: '模拟账户权益曲线' })).toBeVisible()
  await page.getByRole('tab', { name: '逐日权益' }).click()
  await expect(page.getByRole('columnheader', { name: '下期保护线' })).toBeVisible()
  await page.getByRole('button', { name: '返回主界面' }).click()
  await expect(page.getByRole('button', { name: '模拟测试', exact: true })).toBeVisible()
})
