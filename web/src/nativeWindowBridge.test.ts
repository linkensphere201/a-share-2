// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  dockNativeWindow,
  focusNativeWindow,
  popOutNativeWindow,
  readPopoutTarget,
} from './nativeWindowBridge'

afterEach(() => {
  delete window.pywebview
  vi.restoreAllMocks()
})

describe('native window bridge', () => {
  it('reads an exact pop-out host target from the URL', () => {
    expect(readPopoutTarget({ search: '?v=1&popoutGroupId=group-a&popoutWindowId=chart-b' } as Location)).toEqual({
      groupId: 'group-a', windowId: 'chart-b',
    })
    expect(readPopoutTarget({ search: '?popoutGroupId=group-a' } as Location)).toBeUndefined()
  })

  it('forwards bounded native window commands to pywebview', async () => {
    const pop = vi.fn().mockResolvedValue({ ok: true, state: 'opened' })
    const dock = vi.fn().mockResolvedValue({ ok: true, state: 'docked' })
    const focus = vi.fn().mockResolvedValue({ ok: true, state: 'focused' })
    window.pywebview = { api: { pop_out_window: pop, dock_window: dock, focus_window: focus } }
    const target = { groupId: 'group-a', windowId: 'chart-b' }

    await popOutNativeWindow(target, 'Chart', { width: 1000, height: 700 })
    await dockNativeWindow(target)
    await focusNativeWindow(target)

    expect(pop).toHaveBeenCalledWith('group-a', 'chart-b', 'Chart', { width: 1000, height: 700 })
    expect(dock).toHaveBeenCalledWith('group-a', 'chart-b')
    expect(focus).toHaveBeenCalledWith('group-a', 'chart-b')
  })
})
