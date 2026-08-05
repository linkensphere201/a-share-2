export const dailyNoteStorageKey = 'stock-harness.daily-note.v2'
export const legacyDailyNoteStorageKey = 'stock-harness.daily-notes.v1'
export const dailyNotePositionStorageKey = 'stock-harness.daily-note-position.v1'

interface DailyNoteRecord {
  version: 2
  content: string
  updatedAt: string
}

interface LegacyDailyNoteRecord {
  content?: unknown
  updatedAt?: unknown
}

function localDateKey(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function loadDailyNote(storage: Storage = window.localStorage): string {
  try {
    const record = JSON.parse(storage.getItem(dailyNoteStorageKey) ?? '') as Partial<DailyNoteRecord>
    if (record.version === 2 && typeof record.content === 'string') return record.content
  } catch {
    // Fall through to the legacy migration path.
  }
  return migrateLegacyNote(storage)
}

export function saveDailyNote(content: string, storage: Storage = window.localStorage): void {
  const record: DailyNoteRecord = { version: 2, content, updatedAt: new Date().toISOString() }
  storage.setItem(dailyNoteStorageKey, JSON.stringify(record))
}

export function loadDailyNoteTop(storage: Storage = window.localStorage): number {
  try {
    const value = JSON.parse(storage.getItem(dailyNotePositionStorageKey) ?? '') as { top?: unknown }
    return typeof value.top === 'number' && Number.isFinite(value.top) ? value.top : 58
  } catch {
    return 58
  }
}

export function saveDailyNoteTop(top: number, storage: Storage = window.localStorage): void {
  storage.setItem(dailyNotePositionStorageKey, JSON.stringify({ top }))
}

function migrateLegacyNote(storage: Storage): string {
  try {
    const legacy = JSON.parse(storage.getItem(legacyDailyNoteStorageKey) ?? '') as {
      version?: unknown
      notes?: Record<string, LegacyDailyNoteRecord>
    }
    if (legacy.version !== 1 || !legacy.notes || typeof legacy.notes !== 'object') return ''
    const entries = Object.entries(legacy.notes)
      .filter((entry): entry is [string, { content: string; updatedAt?: unknown }] => typeof entry[1]?.content === 'string')
    const today = entries.find(([date]) => date === localDateKey())
    const selected = today ?? entries.sort((left, right) => String(right[1].updatedAt ?? '').localeCompare(String(left[1].updatedAt ?? '')))[0]
    const content = selected?.[1].content ?? ''
    saveDailyNote(content, storage)
    return content
  } catch {
    return ''
  }
}
