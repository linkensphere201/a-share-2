import { describe, expect, it } from 'vitest'
import {
  collectLayoutWindowIds,
  createLayoutTree,
  parseLayoutTree,
  removeLayoutWindow,
  splitLayoutWindow,
  swapLayoutWindows,
  updateSplitRatio,
  validateLayoutTree,
} from './layoutTree'

describe('window layout tree', () => {
  it('builds deterministic valid layouts for one through four windows', () => {
    for (let count = 1; count <= 4; count += 1) {
      const ids = Array.from({ length: count }, (_, index) => `window-${index}`)
      const layout = createLayoutTree(ids, `group-${count}`)
      expect(collectLayoutWindowIds(layout)).toEqual(ids)
      expect(validateLayoutTree(layout, ids)).toEqual([])
    }
  })

  it('splits a leaf and collapses the redundant parent on removal', () => {
    const initial = createLayoutTree(['window-a'])
    const split = splitLayoutWindow(initial, 'window-a', 'window-b', 'horizontal', 'split-new', 'leaf-new')
    expect(collectLayoutWindowIds(split)).toEqual(['window-a', 'window-b'])
    expect(validateLayoutTree(split, ['window-a', 'window-b'])).toEqual([])

    const removed = removeLayoutWindow(split, 'window-a')!
    expect(removed.type).toBe('window')
    expect(collectLayoutWindowIds(removed)).toEqual(['window-b'])
  })

  it('updates bounded ratios and swaps leaves without changing structure', () => {
    const initial = createLayoutTree(['window-a', 'window-b'])
    expect(initial.type).toBe('split')
    if (initial.type !== 'split') return
    const resized = updateSplitRatio(initial, initial.id, 0.99)
    expect(resized.type === 'split' && resized.ratio).toBe(0.85)
    expect(collectLayoutWindowIds(swapLayoutWindows(resized, 'window-a', 'window-b'))).toEqual(['window-b', 'window-a'])
  })

  it('rejects duplicate, missing, and unknown windows', () => {
    const parsed = parseLayoutTree({
      type: 'split', id: 'root', direction: 'vertical', ratio: 0.5,
      first: { type: 'window', id: 'leaf-a', windowId: 'window-a' },
      second: { type: 'window', id: 'leaf-b', windowId: 'window-a' },
    })!
    expect(validateLayoutTree(parsed, ['window-a', 'window-b'])).toEqual([
      'duplicate window: window-a',
      'missing window: window-b',
    ])
  })
})
