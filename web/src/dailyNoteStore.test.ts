// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  dailyNotePositionStorageKey,
  dailyNoteStorageKey,
  legacyDailyNoteStorageKey,
  loadDailyNote,
  loadDailyNoteTop,
  saveDailyNote,
  saveDailyNoteTop,
} from './dailyNoteStore'

afterEach(() => window.localStorage.clear())

describe('dailyNoteStore', () => {
  it('persists one global note', () => {
    saveDailyNote('核心确认')

    expect(loadDailyNote()).toBe('核心确认')
    expect(window.localStorage.getItem(dailyNoteStorageKey)).toContain('核心确认')
  })

  it('migrates the most recently updated legacy dated note', () => {
    window.localStorage.setItem(legacyDailyNoteStorageKey, JSON.stringify({
      version: 1,
      notes: {
        '2020-01-01': { content: '旧内容', updatedAt: '2026-08-01T10:00:00Z' },
        '2020-01-02': { content: '最新内容', updatedAt: '2026-08-02T10:00:00Z' },
      },
    }))

    expect(loadDailyNote()).toBe('最新内容')
    expect(window.localStorage.getItem(dailyNoteStorageKey)).toContain('最新内容')
  })

  it('recovers defaults and persists the minimized top position', () => {
    window.localStorage.setItem(dailyNoteStorageKey, '{bad json')
    window.localStorage.setItem(dailyNotePositionStorageKey, '{bad json')
    expect(loadDailyNote()).toBe('')
    expect(loadDailyNoteTop()).toBe(58)
    saveDailyNoteTop(240)
    expect(loadDailyNoteTop()).toBe(240)
  })
})
