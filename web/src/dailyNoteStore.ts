export const dailyNoteStorageKey = 'stock-harness.daily-notes.v1'

interface StoredDailyNote {
  content: string
  updatedAt: string
}

interface DailyNoteStore {
  version: 1
  notes: Record<string, StoredDailyNote>
}

const emptyStore = (): DailyNoteStore => ({ version: 1, notes: {} })

export function localDateKey(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function shiftDate(dateKey: string, days: number): string {
  const date = new Date(`${dateKey}T12:00:00`)
  date.setDate(date.getDate() + days)
  return localDateKey(date)
}

export function loadDailyNote(dateKey: string, storage: Storage = window.localStorage): string {
  return loadStore(storage).notes[dateKey]?.content ?? ''
}

export function saveDailyNote(dateKey: string, content: string, storage: Storage = window.localStorage): void {
  const store = loadStore(storage)
  store.notes[dateKey] = { content, updatedAt: new Date().toISOString() }
  storage.setItem(dailyNoteStorageKey, JSON.stringify(store))
}

function loadStore(storage: Storage): DailyNoteStore {
  try {
    const value = JSON.parse(storage.getItem(dailyNoteStorageKey) ?? '') as Partial<DailyNoteStore>
    if (value.version !== 1 || !value.notes || typeof value.notes !== 'object') return emptyStore()
    return { version: 1, notes: value.notes as Record<string, StoredDailyNote> }
  } catch {
    return emptyStore()
  }
}
