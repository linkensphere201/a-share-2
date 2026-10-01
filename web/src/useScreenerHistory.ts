import { useCallback, useEffect, useRef, useState } from 'react'
import { listScreenerActivity, listScreenerHistory, loadScreenerRun, type ScreenerRun } from './screenerClient'
import { useSerialPolling } from './useResultQuery'

export const selectedRunKey = 'stock-harness.screener.selected-run.v1'

function ordered(values: ScreenerRun[]) {
  return values.sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running')
    || b.started_at_ms - a.started_at_ms || b.run_id.localeCompare(a.run_id))
}

export function useScreenerHistory(onError: (error: unknown) => void) {
  const [runs, setRuns] = useState<ScreenerRun[]>([])
  const values = useRef<ScreenerRun[]>([])
  const [selectedId, setSelectedId] = useState<string | undefined>(() => localStorage.getItem(selectedRunKey) ?? undefined)
  const selection = useRef(selectedId)
  selection.current = selectedId
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const cursor = useRef<string | undefined>(undefined)
  const exhausted = useRef(false)
  const request = useRef<AbortController | null>(null)
  const deleted = useRef(new Set<string>())
  const errorHandler = useRef(onError)
  errorHandler.current = onError

  const publish = useCallback((items: ScreenerRun[]) => {
    values.current = ordered(items.filter(item => !deleted.current.has(item.run_id)))
    setRuns(values.current)
  }, [])

  const merge = useCallback((incoming: ScreenerRun[], overwrite = true) => {
    const combined = new Map(values.current.map(item => [item.run_id, item]))
    for (const item of incoming) {
      const old = combined.get(item.run_id)
      if (!old || (overwrite && !(old.status !== 'running' && item.status === 'running'))) combined.set(item.run_id, item)
    }
    publish([...combined.values()])
  }, [publish])

  const loadMore = useCallback(async (refresh = false) => {
    if (request.current || (exhausted.current && !refresh)) return
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    try {
      const page = await listScreenerHistory(refresh ? undefined : cursor.current, controller.signal)
      if (controller.signal.aborted) return
      merge(page.items, false)
      cursor.current = page.next_cursor ?? undefined
      exhausted.current = !page.has_more
      setHasMore(Boolean(page.has_more))
      const saved = selection.current
      if (saved && !values.current.some(item => item.run_id === saved)) {
        try {
          const run = await loadScreenerRun(saved, controller.signal)
          if (!controller.signal.aborted && run.run_id === saved) merge([run])
        } catch { /* A manually deleted saved selection falls back to visible history. */ }
      }
      if (!controller.signal.aborted) setSelectedId(current =>
        values.current.some(item => item.run_id === current) ? current : values.current[0]?.run_id)
    } catch (error) {
      if (!controller.signal.aborted) errorHandler.current(error)
    } finally {
      if (request.current === controller) { request.current = null; setLoading(false) }
    }
  }, [merge])

  useEffect(() => {
    void loadMore()
    return () => { request.current?.abort(); request.current = null }
  }, [loadMore])

  useSerialPolling('screener-activity', 1000, async signal => {
    const known = values.current.filter(item => item.status === 'running').map(item => item.run_id)
    const updates = await listScreenerActivity(known, signal)
    if (signal.aborted) return
    // Runs explicitly removed elsewhere must not remain stuck in a running state.
    const missing = new Set(known.filter(id => !updates.some(item => item.run_id === id)))
    publish(values.current.filter(item => !missing.has(item.run_id)))
    merge(updates)
  }, error => errorHandler.current(error))

  const select = useCallback((run?: ScreenerRun) => setSelectedId(run?.run_id), [])
  const add = useCallback((items: ScreenerRun[]) => {
    merge(items)
    if (items.length) setSelectedId(items[0].run_id)
  }, [merge])
  const remove = useCallback((id: string) => {
    deleted.current.add(id)
    publish(values.current.filter(item => item.run_id !== id))
    setSelectedId(current => current === id ? values.current[0]?.run_id : current)
  }, [publish])

  return { runs, selectedRun: runs.find(item => item.run_id === selectedId), setSelectedRun: select,
    loading, hasMore, loadMore, add, remove }
}
