// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  createDefaultWorkspace,
  legacyWorkspaceStorageKey,
  loadWorkspace,
  workspaceStorageKey,
} from './workspace'

afterEach(() => window.localStorage.clear())

describe('workspace persistence', () => {
  it('falls back when persisted state is invalid', () => {
    window.localStorage.setItem(workspaceStorageKey, '{invalid')
    expect(loadWorkspace()).toEqual(createDefaultWorkspace())
  })

  it('normalizes missing focus and maximize references', () => {
    const state = createDefaultWorkspace()
    state.groups[0].focusedWindowId = 'missing'
    state.groups[0].maximizedWindowId = 'missing'
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))

    const loaded = loadWorkspace()
    expect(loaded.groups[0].focusedWindowId).toBe('window-primary')
    expect(loaded.groups[0].maximizedWindowId).toBeUndefined()
  })

  it('prefers valid v2 state over legacy state', () => {
    const state = createDefaultWorkspace()
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    window.localStorage.setItem(legacyWorkspaceStorageKey, JSON.stringify([{
      id: 'legacy',
      instrument: { symbol: 'legacy', name: 'legacy', kind: 'stock', exchange: 'SZ', rows: 1 },
      range: '1Y',
      priceMode: 'normal',
    }]))

    expect(loadWorkspace()).toEqual(state)
  })
})
