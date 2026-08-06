import { useEffect, useMemo, useState } from 'react'
import { Network, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { customGroupRoleDefinitions, type CustomGroupMember } from './customGroupRoles'
import type { Instrument } from './workspace'

type CustomGroupDetail = {
  id: string
  name: string
  description: string
  members: CustomGroupMember[]
}

export function CustomGroupMindMap({
  group,
  onSelect,
  onClose,
}: {
  group: Instrument
  onSelect: (instrument: Instrument) => void
  onClose: () => void
}) {
  const [detail, setDetail] = useState<CustomGroupDetail>()
  const [failed, setFailed] = useState(false)
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

  const unclassified = useMemo(
    () => detail?.members.filter(member => !member.role) ?? [],
    [detail],
  )

  return createPortal(<section
    className="custom-group-mind-map"
    role="dialog"
    aria-label={`${group.name} 思维导图`}
    onPointerDown={event => event.stopPropagation()}
  >
    <header>
      <div><Network size={16}/><strong>{detail?.name ?? group.name}</strong><span>思维导图</span></div>
      <button className="icon-button" title="关闭" aria-label="关闭自选集合思维导图" onClick={onClose}><X size={16}/></button>
    </header>
    <div className="custom-group-map-body">
      <div className="custom-group-map-root">
        <Network size={18}/>
        <strong>{detail?.name ?? group.name}</strong>
        <span>{detail?.description || '自选集合'}</span>
      </div>
      <div className="custom-group-map-trunk" aria-hidden="true"/>
      <div className="custom-group-map-branches">
        {!detail && !failed && <div className="custom-group-map-state">正在加载集合结构</div>}
        {failed && <div className="custom-group-map-state error">集合结构加载失败</div>}
        {detail && customGroupRoleDefinitions.map(role => {
          const members = detail.members.filter(member => member.role === role.value)
          return <section className={`custom-group-map-branch role-${role.value}`} key={role.value}>
            <div className="custom-group-map-role">
              <strong>{role.label}</strong>
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
                <strong>{member.name}</strong>
                <small>{member.symbol}</small>
                {(member.note || member.tags.length > 0) && <span>{member.note || member.tags.join(' · ')}</span>}
              </button>)}
            </div>
          </section>
        })}
        {detail && unclassified.length > 0 && <section className="custom-group-map-unclassified">
          <strong>未分类</strong>
          <span>{unclassified.map(member => member.name).join('、')}</span>
        </section>}
      </div>
    </div>
  </section>, document.body)
}
