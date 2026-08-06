import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Grip, Move, Network, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { customGroupRoleDefinitions, type CustomGroupMember } from './customGroupRoles'
import type { Instrument } from './workspace'

export type MindMapAnchor = {
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
}

type CustomGroupDetail = {
  id: string
  name: string
  description: string
  members: CustomGroupMember[]
}

export function CustomGroupMindMap({
  group,
  anchor,
  onSelect,
  onClose,
}: {
  group: Instrument
  anchor: MindMapAnchor
  onSelect: (instrument: Instrument) => void
  onClose: () => void
}) {
  const [detail, setDetail] = useState<CustomGroupDetail>()
  const [failed, setFailed] = useState(false)
  const [geometry, setGeometry] = useState(() => initialGeometry(anchor))
  const interaction = useRef<{
    mode: 'move' | 'resize'
    pointerId: number
    startX: number
    startY: number
    start: PanelGeometry
  } | undefined>(undefined)
  const groupId = group.symbol.startsWith('CUSTOM:') ? group.symbol.slice(7) : ''

  useEffect(() => {
    const controller = new AbortController()
    setDetail(undefined)
    setFailed(false)
    fetch(`/api/custom-groups/${encodeURIComponent(groupId)}`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<CustomGroupDetail>
      })
      .then(setDetail)
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setFailed(true)
      })
    return () => controller.abort()
  }, [groupId])

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const active = interaction.current
      if (!active || event.pointerId !== active.pointerId) return
      const deltaX = event.clientX - active.startX
      const deltaY = event.clientY - active.startY
      setGeometry(active.mode === 'move'
        ? clampGeometry({ ...active.start, left: active.start.left + deltaX, top: active.start.top + deltaY })
        : clampGeometry({ ...active.start, width: active.start.width + deltaX, height: active.start.height + deltaY }))
    }
    const stop = (event: PointerEvent) => {
      if (interaction.current?.pointerId === event.pointerId) interaction.current = undefined
    }
    const resizeWindow = () => setGeometry(current => clampGeometry(current))
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    window.addEventListener('resize', resizeWindow)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      window.removeEventListener('resize', resizeWindow)
    }
  }, [])

  const unclassified = useMemo(
    () => detail?.members.filter(member => !member.role) ?? [],
    [detail],
  )

  const title = `板块分析 - ${detail?.name ?? group.name}`
  const connection = connector(anchor, geometry)
  const beginInteraction = (mode: 'move' | 'resize') => (event: ReactPointerEvent) => {
    event.preventDefault()
    event.stopPropagation()
    interaction.current = {
      mode,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      start: geometry,
    }
  }

  return createPortal(<>
    <div className="custom-group-map-interaction-shield" aria-hidden="true"/>
    <svg className="custom-group-map-connector" aria-hidden="true">
      <line x1={connection.x1} y1={connection.y1} x2={connection.x2} y2={connection.y2}/>
      <circle cx={connection.x1} cy={connection.y1} r="2.5"/>
      <circle cx={connection.x2} cy={connection.y2} r="2.5"/>
    </svg>
    <section
      className="custom-group-mind-map"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={geometry}
      onPointerDown={event => event.stopPropagation()}
    >
    <div className="custom-group-map-controls">
      <button
        className="icon-button custom-group-map-move"
        title="移动"
        aria-label="移动板块分析窗口"
        onPointerDown={beginInteraction('move')}
      ><Move size={14}/></button>
      <button className="icon-button" title="关闭" aria-label="关闭板块分析窗口" onClick={onClose}><X size={14}/></button>
    </div>
    <div className="custom-group-map-body">
      <div className="custom-group-map-root">
        <Network size={18}/>
        <span>{detail?.name ?? group.name}</span>
      </div>
      <div className="custom-group-map-trunk" aria-hidden="true"/>
      <div className="custom-group-map-branches">
        {!detail && !failed && <div className="custom-group-map-state">正在加载集合结构</div>}
        {failed && <div className="custom-group-map-state error">集合结构加载失败</div>}
        {detail && customGroupRoleDefinitions.map(role => {
          const members = detail.members.filter(member => member.role === role.value)
          return <section className={`custom-group-map-branch role-${role.value}`} key={role.value}>
            <div className="custom-group-map-role">
              <span>{role.label}</span>
              <span>{role.description}</span>
            </div>
            <div className="custom-group-map-nodes">
              {members.length === 0 && <span className="custom-group-map-empty">暂无标的</span>}
              {members.map(member => <button
                key={member.symbol}
                disabled={member.available === false}
                title={member.note || member.tags.join(' · ')}
                onClick={() => {
                  onSelect({ ...member, rows: member.rows ?? 0 })
                  onClose()
                }}
              >
                <span className="custom-group-map-name">{member.name}</span>
                {(member.note || member.tags.length > 0) && <span>{member.note || member.tags.join(' · ')}</span>}
              </button>)}
            </div>
          </section>
        })}
        {detail && unclassified.length > 0 && <section className="custom-group-map-unclassified">
          <span>未分类</span>
          <span>{unclassified.map(member => member.name).join('、')}</span>
        </section>}
      </div>
    </div>
    <button
      className="custom-group-map-resize"
      title="调整窗口大小"
      aria-label="调整板块分析窗口大小"
      onPointerDown={beginInteraction('resize')}
    ><Grip size={14}/></button>
  </section></>, document.body)
}

type PanelGeometry = { left: number; top: number; width: number; height: number }

function initialGeometry(anchor: MindMapAnchor): PanelGeometry {
  const width = Math.min(780, Math.max(320, window.innerWidth - 32))
  const height = Math.min(640, Math.max(300, window.innerHeight - 96))
  const gap = 18
  const left = anchor.right + gap + width <= window.innerWidth
    ? anchor.right + gap
    : anchor.left - gap - width >= 0 ? anchor.left - gap - width : (window.innerWidth - width) / 2
  return clampGeometry({ left, top: anchor.top - 38, width, height })
}

function clampGeometry(value: PanelGeometry): PanelGeometry {
  const margin = 8
  const minWidth = Math.min(520, window.innerWidth - margin * 2)
  const minHeight = Math.min(360, window.innerHeight - margin * 2)
  const width = Math.max(minWidth, Math.min(value.width, window.innerWidth - margin * 2))
  const height = Math.max(minHeight, Math.min(value.height, window.innerHeight - margin * 2))
  return {
    width,
    height,
    left: Math.max(margin, Math.min(value.left, window.innerWidth - width - margin)),
    top: Math.max(margin, Math.min(value.top, window.innerHeight - height - margin)),
  }
}

function connector(anchor: MindMapAnchor, panel: PanelGeometry) {
  const x1 = anchor.left + anchor.width / 2
  const y1 = anchor.top + anchor.height / 2
  const x2 = panel.left
  const y2 = panel.top + panel.height / 2
  return { x1, y1, x2, y2 }
}
