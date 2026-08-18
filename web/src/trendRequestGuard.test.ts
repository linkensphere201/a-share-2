import { describe, expect, it } from 'vitest'
import { beginTrendRequest, isCurrentTrendRequest } from './trendRequestGuard'


describe('trend request guard', () => {
  it('accepts only the latest request for a chart window', () => {
    const generations = new Map<string, number>()
    const scope = { groupId: 'group-1', windowId: 'chart-1', symbol: '000001.SZ' }
    const first = beginTrendRequest(generations, scope)
    const second = beginTrendRequest(generations, scope)

    expect(isCurrentTrendRequest(generations, first, scope)).toBe(false)
    expect(isCurrentTrendRequest(generations, second, scope)).toBe(true)
  })

  it('rejects completion after the group or displayed symbol changes', () => {
    const generations = new Map<string, number>()
    const scope = { groupId: 'group-1', windowId: 'chart-1', symbol: '000001.SZ' }
    const token = beginTrendRequest(generations, scope)

    expect(isCurrentTrendRequest(generations, token, {
      ...scope, symbol: '600519.SH',
    })).toBe(false)
    expect(isCurrentTrendRequest(generations, token, {
      ...scope, groupId: 'group-2',
    })).toBe(false)
    expect(isCurrentTrendRequest(generations, token, undefined)).toBe(false)
  })
})
