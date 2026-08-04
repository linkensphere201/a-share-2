import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, CircleX } from 'lucide-react'

type RuntimeEvent = {
  event_id: number
  timestamp: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL'
  source: string
  logger: string
  message: string
}

export function RuntimeEventBar() {
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [open, setOpen] = useState(false)
  const lastEventId = useRef(0)

  useEffect(() => {
    let stopped = false
    const poll = () => {
      fetch(`/api/runtime-events?min_level=WARNING&after_id=${lastEventId.current}&limit=50`)
        .then(response => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json() as Promise<{ items: RuntimeEvent[] }>
        })
        .then(body => {
          const validEvents = body.items.filter(isRuntimeEvent)
          if (stopped || validEvents.length === 0) return
          lastEventId.current = validEvents.at(-1)!.event_id
          setEvents(current => [...current, ...validEvents].slice(-50))
        })
        .catch(error => console.warn('[RuntimeEventBar] Event polling failed', error))
    }
    poll()
    const timer = window.setInterval(poll, 5000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  const latest = events.at(-1)
  const Icon = latest?.level === 'ERROR' || latest?.level === 'CRITICAL' ? CircleX : AlertTriangle
  return (
    <div className={latest ? `runtime-events ${latest.level.toLowerCase()}` : 'runtime-events'}>
      <button
        className="runtime-event-summary"
        disabled={!latest}
        title={latest ? '查看运行事件' : '暂无警告或错误'}
        onClick={() => setOpen(value => !value)}
      >
        {latest ? <Icon size={12}/> : <i/>}
        <span>{latest ? latest.message : '运行正常'}</span>
        {events.length > 0 && <b>{events.length}</b>}
        {latest && (open ? <ChevronDown size={11}/> : <ChevronUp size={11}/>)}
      </button>
      {open && latest && (
        <div className="runtime-event-panel">
          <header><strong>运行事件</strong><button onClick={() => setEvents([])}>清除</button></header>
          {events.slice().reverse().map(event => (
            <div className={`runtime-event-row ${event.level.toLowerCase()}`} key={event.event_id}>
              <time>{new Date(event.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</time>
              <strong>{event.level}</strong>
              <span>{event.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function isRuntimeEvent(value: RuntimeEvent): value is RuntimeEvent {
  return typeof value?.event_id === 'number'
    && typeof value.message === 'string'
    && ['WARNING', 'ERROR', 'CRITICAL'].includes(value.level)
}
