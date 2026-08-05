// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import { dailyNoteStorageKey, loadDailyNote, saveDailyNote, shiftDate } from './dailyNoteStore'

afterEach(() => window.localStorage.clear())

describe('dailyNoteStore', () => {
  it('keeps notes separate by local calendar date', () => {
    saveDailyNote('2026-08-05', '分歧日')
    saveDailyNote('2026-08-06', '核心确认')

    expect(loadDailyNote('2026-08-05')).toBe('分歧日')
    expect(loadDailyNote('2026-08-06')).toBe('核心确认')
    expect(loadDailyNote('2026-08-07')).toBe('')
  })

  it('recovers from corrupt storage and shifts dates across month boundaries', () => {
    window.localStorage.setItem(dailyNoteStorageKey, '{bad json')
    expect(loadDailyNote('2026-08-06')).toBe('')
    expect(shiftDate('2026-08-01', -1)).toBe('2026-07-31')
  })
})
