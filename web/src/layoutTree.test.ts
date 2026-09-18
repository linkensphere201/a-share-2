import { describe, expect, it } from 'vitest'
import {
  collectLayoutWindowIds,
  moveLayoutWindow,
  createLayoutTree,
  parseLayoutTree,
  removeLayoutWindow,
  splitLayoutWindow,
  swapLayoutWindows,
  updateSplitRatio,
  validateLayoutTree,
} from './layoutTree'

describe('window layout tree', () => {
  it('moves nested leaves to each edge without losing identity or duplicating nodes', () => {
    const ids = ['a', 'b', 'c', 'd']
    const layout = createLayoutTree(ids)
    for (const source of ids) for (const target of ids) {
      for (const position of ['left', 'right', 'top', 'bottom', 'center'] as const) {
        const moved = moveLayoutWindow(layout, source, target, position, 'new-split')
        expect(validateLayoutTree(moved, ids)).toEqual([])
        expect(collectLayoutWindowIds(layout)).toEqual(ids)
        if (source === target) expect(moved).toBe(layout)
      }
    }
    expect(moveLayoutWindow(layout, 'unknown', 'a', 'left', 'new')).toBe(layout)
    expect(moveLayoutWindow(layout, 'a', 'unknown', 'left', 'new')).toBe(layout)
  })

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
