import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { Instrument, WindowGroupState } from './workspace'

const channelName = 'stock-harness.transient-chart-cast.v1'
const chartKey = (groupId: string, chartId: string) => JSON.stringify([groupId, chartId])

type TransientChartMessage = {
  action: 'cast' | 'clear'
  groupId: string
  chartId: string
  instrument?: Instrument
}

export function useTemporaryChartCasting(group: WindowGroupState) {
  const [instruments, setInstruments] = useState<Record<string, Instrument>>({})
  const channelRef = useRef<BroadcastChannel | undefined>(undefined)

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return
    const channel = new BroadcastChannel(channelName)
    channelRef.current = channel
    channel.onmessage = event => applyMessage(setInstruments, event.data as TransientChartMessage)
    return () => {
      channelRef.current = undefined
      channel.close()
    }
  }, [])

  const displayedGroup = useMemo<WindowGroupState>(() => ({
    ...group,
    windows: group.windows.map(item => {
      const instrument = item.type === 'chart'
        ? instruments[chartKey(group.id, item.id)]
        : undefined
      return item.type === 'chart' && instrument
        ? { ...item, instrument, chart: { ...item.chart, visibleRange: undefined } }
        : item
    }),
  }), [group, instruments])

  const setTemporaryInstrument = useCallback((chartId: string, instrument?: Instrument) => {
    const message: TransientChartMessage = {
      action: instrument ? 'cast' : 'clear',
      groupId: group.id,
      chartId,
      instrument,
    }
    applyMessage(setInstruments, message)
    channelRef.current?.postMessage(message)
  }, [group.id])

  return { displayedGroup, setTemporaryInstrument }
}

function applyMessage(
  setInstruments: Dispatch<SetStateAction<Record<string, Instrument>>>,
  message: TransientChartMessage,
) {
  if (!message || !['cast', 'clear'].includes(message.action)) return
  const key = chartKey(message.groupId, message.chartId)
  setInstruments(current => {
    if (message.action === 'cast' && message.instrument) {
      return { ...current, [key]: message.instrument }
    }
    if (!(key in current)) return current
    const next = { ...current }
    delete next[key]
    return next
  })
}
