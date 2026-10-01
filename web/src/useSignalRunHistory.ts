import { useCallback, useRef, useState } from 'react'
import { listSignalRuns, loadSignalRun, type SignalRun } from './signalReviewClient'
import { useResultQuery, useSerialPolling } from './useResultQuery'

function ordered(values: SignalRun[]) {
  return values.sort((a, b) => b.effective_date.localeCompare(a.effective_date)
    || b.revision - a.revision || b.started_at_ms - a.started_at_ms || b.run_id.localeCompare(a.run_id))
}

type State = { owner?: string; runs: SignalRun[]; selected?: string }

export function useSignalRunHistory(signalId: string | undefined, onError: (error: unknown) => void) {
  const [state, setState] = useState<State>({ runs: [] })
  const owner = useRef(signalId)
  owner.current = signalId
  const runs = state.owner === signalId ? state.runs : []
  const selectedRun = runs.find(item => item.run_id === state.selected)
  const activeRun = runs.find(item => item.status === 'running')

  const merge = useCallback((incoming: SignalRun[], select?: string) => {
    if (owner.current !== signalId) return
    setState(previous => {
      const current = previous.owner === signalId ? previous : { runs: [] }
      const values = new Map(current.runs.map(item => [item.run_id, item]))
      for (const item of incoming) {
        if (item.signal_id !== signalId) continue
        const existing = values.get(item.run_id)
        if (existing?.status !== 'running' && item.status === 'running' && existing) continue
        values.set(item.run_id, item)
      }
      const next = ordered([...values.values()])
      return { owner: signalId, runs: next, selected: select ?? current.selected ?? next[0]?.run_id }
    })
  }, [signalId])

  useResultQuery(signalId, signal => listSignalRuns(signalId!, signal), values => merge(values), onError)
  useSerialPolling(activeRun ? `${signalId}:${activeRun.run_id}` : undefined, 1200, async signal => {
    const next = await loadSignalRun(activeRun!.run_id, signal)
    if (signal.aborted) return
    const values = await listSignalRuns(signalId!, signal)
    if (signal.aborted) return
    merge([...values, next])
  }, onError)

  const addRun = useCallback((run: SignalRun) => merge([run], run.run_id), [merge])
  const select = useCallback((run?: SignalRun) => {
    if (run) addRun(run)
    else setState(previous => ({ ...previous, selected: undefined }))
  }, [addRun])
  return { runs, selectedRun, activeRun, setSelectedRun: select, addRun }
}
