// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DailyNote } from './DailyNote'
import { dailyNotePositionStorageKey, dailyNoteStorageKey } from './dailyNoteStore'

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

describe('DailyNote', () => {
  it('is read-only until edit is clicked and persists rendered markdown', async () => {
    const user = userEvent.setup()
    const view = render(<DailyNote/>)

    expect(screen.getByRole('button', { name: '展开每日便签' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '展开每日便签' }))
    expect(screen.queryByRole('textbox', { name: '每日便签内容' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '编辑每日便签' }))
    await user.type(screen.getByRole('textbox', { name: '每日便签内容' }), '# 复盘{enter}- 核心板块')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByRole('heading', { name: '复盘' })).toBeTruthy()
    expect(screen.getByText('核心板块')).toBeTruthy()
    expect(window.localStorage.getItem(dailyNoteStorageKey)).toContain('核心板块')

    view.unmount()
    render(<DailyNote/>)
    await user.click(screen.getByRole('button', { name: '展开每日便签' }))
    expect(screen.getByRole('heading', { name: '复盘' })).toBeTruthy()
  })

  it('cancels drafts and minimizes to one draggable icon that snaps left', async () => {
    const user = userEvent.setup()
    render(<DailyNote/>)

    await user.click(screen.getByRole('button', { name: '展开每日便签' }))
    await user.click(screen.getByRole('button', { name: '编辑每日便签' }))
    await user.type(screen.getByRole('textbox', { name: '每日便签内容' }), '未保存')
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByText('未保存')).toBeNull()

    expect(screen.queryByLabelText('便签日期')).toBeNull()

    await user.click(screen.getByRole('button', { name: '最小化每日便签' }))
    expect(screen.queryByLabelText('每日便签')).toBeNull()
    const icon = screen.getByRole('button', { name: '展开每日便签' })
    fireEvent.pointerDown(icon, { pointerId: 1, button: 0, clientX: 20, clientY: 70 })
    fireEvent.pointerMove(icon, { pointerId: 1, clientX: 120, clientY: 220 })
    fireEvent.pointerUp(icon, { pointerId: 1, clientX: 120, clientY: 220 })
    expect(icon.style.left).toBe('10px')
    expect(icon.style.top).toBe('208px')
    expect(window.localStorage.getItem(dailyNotePositionStorageKey)).toContain('208')

    await user.click(icon)
    expect(screen.getByLabelText('每日便签')).toBeTruthy()
  })
})
