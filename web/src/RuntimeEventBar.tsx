import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, CircleX, RefreshCw } from 'lucide-react'

type RuntimeEvent = {
  event_id: number
  timestamp: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL'
  source: string
  logger: string
  message: string
}

type UpdateStatus = {
  state: string
  trigger?: string | null
  completed_at?: string | null
  rows_changed?: number
  error?: string | null
}

export function RuntimeEventBar() {
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [open, setOpen] = useState(false)
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>({ state: 'idle' })
  const [updateNotice, setUpdateNotice] = useState('')
  const lastEventId = useRef(0)
  const updateWasActive = useRef(false)
  const updateNoticeTimer = useRef(0)

  useEffect(() => {
    let stopped = false
    let timer = 0
    let controller: AbortController | undefined
    const schedule = (delay: number) => {
      timer = window.setTimeout(poll, delay)
    }
    const poll = () => {
      if (stopped) return
      controller = new AbortController()
      fetch(`/api/runtime-events?min_level=WARNING&after_id=${lastEventId.current}&limit=50`, {
        signal: controller.signal,
      })
        .then(response => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json() as Promise<{ items: RuntimeEvent[] }>
        })
        .then(body => {
          const validEvents = body.items.filter(isRuntimeEvent)
          if (!stopped && validEvents.length > 0) {
            lastEventId.current = validEvents.at(-1)!.event_id
            setEvents(current => [...current, ...validEvents].slice(-50))
          }
          return fetch('/api/update-status', { signal: controller?.signal })
        })
        .then(response => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json() as Promise<UpdateStatus>
        })
        .then(status => {
          if (stopped) return
          const active = status.state === 'queued' || status.state === 'running'
          if (updateWasActive.current && !active) {
            window.clearTimeout(updateNoticeTimer.current)
            setUpdateNotice(
              status.state === 'error'
                ? '盘后日线刷新失败'
                : status.state === 'warning'
                  ? '盘后日线刷新完成，但存在告警'
                  : `盘后日线刷新完成，更新 ${status.rows_changed ?? 0} 行`,
            )
            updateNoticeTimer.current = window.setTimeout(() => setUpdateNotice(''), 5_000)
          }
          updateWasActive.current = active
          setUpdateStatus(status)
        })
        .catch(error => {
          if ((error as Error).name !== 'AbortError') {
            console.warn('[RuntimeEventBar] Status polling failed', error)
          }
        })
        .finally(() => {
          controller = undefined
          if (!stopped) schedule(2_000)
        })
    }
    schedule(0)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.clearTimeout(updateNoticeTimer.current)
      controller?.abort()
    }
  }, [])

  const latest = events.at(-1)
  const Icon = latest?.level === 'ERROR' || latest?.level === 'CRITICAL' ? CircleX : AlertTriangle
  const updateActive = updateStatus.state === 'queued' || updateStatus.state === 'running'
  const updateMessage = updateActive
    ? updateStatus.state === 'queued' ? '盘后日线刷新已排队' : '正在刷新盘后正式日线'
    : updateNotice
  const summary = updateMessage || latest?.message || '运行正常'
  return (
    <div className={updateActive ? 'runtime-events updating' : latest ? `runtime-events ${latest.level.toLowerCase()}` : 'runtime-events'}>
      <button
        className="runtime-event-summary"
        disabled={!latest}
        title={latest ? '查看运行事件' : updateMessage || '暂无警告或错误'}
        onClick={() => setOpen(value => !value)}
      >
        {updateActive ? <RefreshCw className="runtime-update-spinner" size={12}/> : latest ? <Icon size={12}/> : <i/>}
        <span>{summary}</span>
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
