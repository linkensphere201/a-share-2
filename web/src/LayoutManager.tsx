import { useState } from 'react'
import {
  ArrowLeft,
  BarChart3,
  Check,
  Copy,
  LayoutGrid,
  List,
  PanelLeft,
  PanelTop,
  Plus,
  Redo2,
  Star,
  Undo2,
  Trash2,
} from 'lucide-react'
import { collectLayoutWindowIds, removeLayoutWindow, splitLayoutWindow, swapLayoutWindows, updateSplitRatio, type SplitDirection } from './layoutTree'
import { removeWindowAttachments, validateWindowAttachments } from './windowAttachments'
import { SplitLayout } from './SplitLayout'
import {
  createWindowGroup,
  duplicateWindowGroup,
  type ChartWindowState,
  type Instrument,
  type InstrumentListWindowState,
  type WindowGroupState,
  type WindowGroupTemplate,
  type WorkspaceState,
  type WorkspaceWindowState,
} from './workspace'

type LayoutManagerProps = {
  workspace: WorkspaceState
  onChange: (update: (workspace: WorkspaceState) => WorkspaceState) => void
  onClose: () => void
}

const templates: { id: WindowGroupTemplate; label: string }[] = [
  { id: 'list-chart', label: '列表 + 联动图表' },
  { id: 'comparison', label: '列表 + 双图表' },
  { id: 'four-charts', label: '四图表' },
]

export function LayoutManager({ workspace, onChange, onClose }: LayoutManagerProps) {
  const [selectedGroupId, setSelectedGroupId] = useState(workspace.activeGroupId)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('新窗口组')
  const [template, setTemplate] = useState<WindowGroupTemplate>('list-chart')
  const [direction, setDirection] = useState<SplitDirection>('horizontal')
  const [past, setPast] = useState<WorkspaceState[]>([])
  const [future, setFuture] = useState<WorkspaceState[]>([])
  const group = workspace.groups.find(item => item.id === selectedGroupId) ?? workspace.groups[0]
  const focused = group.windows.find(item => item.id === group.focusedWindowId) ?? group.windows[0]

  const commit = (update: (workspace: WorkspaceState) => WorkspaceState) => {
    const next = update(workspace)
    if (next === workspace) return
    setPast(items => [...items.slice(-49), workspace])
    setFuture([])
    onChange(() => next)
  }

  const updateGroup = (update: (group: WindowGroupState) => WindowGroupState) => {
    commit(current => ({
      ...current,
      groups: current.groups.map(item => item.id === group.id ? update(item) : item),
    }))
  }

  const createGroup = () => {
    const name = newName.trim() || `窗口组 ${workspace.groups.length + 1}`
    const added = createWindowGroup(name, template)
    commit(current => ({ ...current, groups: [...current.groups, added] }))
    setSelectedGroupId(added.id)
    setCreating(false)
    setNewName('新窗口组')
  }

  const duplicateGroup = () => {
    const added = duplicateWindowGroup(group, `${group.name} 副本`)
    commit(current => ({ ...current, groups: [...current.groups, added] }))
    setSelectedGroupId(added.id)
  }

  const deleteGroup = () => {
    if (workspace.groups.length === 1 || group.id === workspace.defaultGroupId) return
    if (!window.confirm(`删除窗口组“${group.name}”？`)) return
    const next = workspace.groups.find(item => item.id !== group.id)!
    commit(current => ({
      ...current,
      activeGroupId: current.activeGroupId === group.id ? next.id : current.activeGroupId,
      groups: current.groups.filter(item => item.id !== group.id),
    }))
    setSelectedGroupId(next.id)
  }

  const openGroup = () => {
    onChange(current => ({ ...current, activeGroupId: group.id }))
    onClose()
  }

  const undo = () => {
    const previous = past.at(-1)
    if (!previous) return
    setPast(items => items.slice(0, -1))
    setFuture(items => [workspace, ...items].slice(0, 50))
    onChange(() => previous)
    if (!previous.groups.some(item => item.id === selectedGroupId)) setSelectedGroupId(previous.activeGroupId)
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setFuture(items => items.slice(1))
    setPast(items => [...items.slice(-49), workspace])
    onChange(() => next)
    if (!next.groups.some(item => item.id === selectedGroupId)) setSelectedGroupId(next.activeGroupId)
  }

  const addWindow = (type: 'instrument-list' | 'chart', mode: ChartWindowState['mode']) => {
    const chartCount = group.windows.filter(item => item.type === 'chart').length
    const listCount = group.windows.filter(item => item.type === 'instrument-list').length
    if (group.windows.length >= 8 || (type === 'chart' ? chartCount >= 6 : listCount >= 4)) return
    const id = `${type === 'chart' ? 'chart' : 'list'}-${crypto.randomUUID()}`
    const instrument = resolveInstrument(group.windows)
    const title = nextWindowTitle(group.windows)
    const added: WorkspaceWindowState = type === 'chart'
      ? { id, type: 'chart', title, mode, instrument, chart: { range: '3Y', priceMode: 'normal', volumeVisible: true, indicator: 'macd' } }
      : { id, type: 'instrument-list', title, mode, content: { mode: 'manual', instruments: [] } }
    updateGroup(current => ({
      ...current,
      windows: [...current.windows, added],
      layout: splitLayoutWindow(
        current.layout,
        current.focusedWindowId,
        id,
        direction,
        `split-${crypto.randomUUID()}`,
        `layout-${crypto.randomUUID()}`,
      ),
      focusedWindowId: id,
      maximizedWindowId: undefined,
    }))
  }

  const removeWindow = () => {
    if (group.windows.length === 1) return
    updateGroup(current => {
      const index = current.windows.findIndex(item => item.id === focused.id)
      const windows = current.windows.filter(item => item.id !== focused.id)
      const layout = removeLayoutWindow(current.layout, focused.id)
      if (!layout) return current
      return {
        ...current,
        layout,
        windows,
        attachments: removeWindowAttachments(current.attachments, focused.id),
        focusedWindowId: windows[Math.min(index, windows.length - 1)].id,
        maximizedWindowId: undefined,
      }
    })
  }

  const updateWindowProperties = (updated: WorkspaceWindowState) => {
    updateGroup(current => {
      const windows = current.windows.map(item => item.id === updated.id ? updated : item)
      let attachments = updated.mode === 'detached'
        ? current.attachments.filter(edge => edge.targetWindowId !== updated.id)
        : current.attachments
      if (updated.type === 'instrument-list' && updated.mode === 'attached') {
        attachments = attachments.filter(edge => !(
          edge.sourceWindowId === updated.id && edge.type === 'show-members'
        ))
      }
      return validateWindowAttachments(attachments, windows).length === 0
        ? { ...current, windows, attachments }
        : current
    })
  }

  const setDriver = (targetId: string, sourceId: string, enabled: boolean) => {
    updateGroup(current => {
      const target = current.windows.find(item => item.id === targetId)
      if (!target || target.mode !== 'attached') return current
      const edgeType = target.type === 'chart' ? 'show-symbol' as const : 'show-members' as const
      const without = current.attachments.filter(edge => !(
        edge.targetWindowId === targetId && edge.sourceWindowId === sourceId
      ))
      const attachments = enabled ? [...without, {
        id: `attachment-${crypto.randomUUID()}`,
        type: edgeType,
        sourceWindowId: sourceId,
        targetWindowId: targetId,
      }] : without
      return validateWindowAttachments(attachments, current.windows).length === 0
        ? { ...current, attachments }
        : current
    })
  }

  const swapFocusedWindow = () => {
    const order = collectLayoutWindowIds(group.layout)
    if (order.length < 2) return
    const index = order.indexOf(focused.id)
    const target = order[(index + 1) % order.length]
    updateGroup(current => ({ ...current, layout: swapLayoutWindows(current.layout, focused.id, target) }))
  }

  return (
    <main className="layout-manager">
      <header className="layout-manager-header">
        <button className="command-button" onClick={onClose}><ArrowLeft size={16}/>返回工作台</button>
        <div><LayoutGrid size={18}/><strong>布局管理</strong><span>窗体组与关联关系</span></div>
        <div className="layout-header-actions">
          <button className="icon-button" title="撤销" aria-label="撤销" disabled={!past.length} onClick={undo}><Undo2 size={16}/></button>
          <button className="icon-button" title="重做" aria-label="重做" disabled={!future.length} onClick={redo}><Redo2 size={16}/></button>
          <button className="primary-button" onClick={openGroup}><Check size={16}/>打开此组</button>
        </div>
      </header>

      <aside className="group-sidebar">
        <div className="group-sidebar-title"><strong>窗口组</strong><button className="icon-button" title="新建窗口组" aria-label="新建窗口组" onClick={() => setCreating(true)}><Plus size={16}/></button></div>
        {creating && (
          <div className="new-group-form">
            <input aria-label="窗口组名称" value={newName} onChange={event => setNewName(event.target.value)}/>
            <select aria-label="窗口组模板" value={template} onChange={event => setTemplate(event.target.value as WindowGroupTemplate)}>
              {templates.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
            <div><button onClick={() => setCreating(false)}>取消</button><button className="primary-button" onClick={createGroup}>创建</button></div>
          </div>
        )}
        <div className="group-list">
          {workspace.groups.map(item => (
            <button key={item.id} className={item.id === group.id ? 'active' : ''} onClick={() => setSelectedGroupId(item.id)}>
              <span><strong>{item.name}</strong><small>{item.windows.length} 个窗口</small></span>
              {item.id === workspace.defaultGroupId && <Star size={13} fill="currentColor"/>}
            </button>
          ))}
        </div>
      </aside>

      <section className="layout-editor">
        <div className="layout-editor-toolbar">
          <input aria-label="重命名窗口组" value={group.name} onChange={event => updateGroup(item => ({ ...item, name: event.target.value }))}/>
          <button className="command-button" disabled={group.id === workspace.defaultGroupId} onClick={() => commit(current => ({ ...current, defaultGroupId: group.id }))}><Star size={15}/>设为默认</button>
          <button className="command-button" onClick={duplicateGroup}><Copy size={15}/>复制</button>
          <button className="danger-button" disabled={workspace.groups.length === 1 || group.id === workspace.defaultGroupId} onClick={deleteGroup}><Trash2 size={15}/>删除组</button>
        </div>
        <div className="layout-editor-body">
          <div className="layout-preview-panel">
            <div className="panel-heading"><strong>布局预览</strong><span>拖动分割线调整比例，点击窗口编辑属性</span></div>
            <div className="layout-preview">
              <SplitLayout
                layout={group.layout}
                renderWindow={id => <LayoutPreviewWindow
                  windowId={id}
                  group={group}
                  onSelect={selected => updateGroup(item => ({ ...item, focusedWindowId: selected }))}
                />}
                onRatioCommit={(id, ratio) => updateGroup(item => ({
                  ...item,
                  layout: updateSplitRatio(item.layout, id, ratio),
                }))}
              />
            </div>
          </div>
          <aside className="layout-inspector">
            <section>
              <label>切分方向</label>
              <div className="segmented-control">
                <button className={direction === 'horizontal' ? 'active' : ''} onClick={() => setDirection('horizontal')}><PanelLeft size={15}/>左右</button>
                <button className={direction === 'vertical' ? 'active' : ''} onClick={() => setDirection('vertical')}><PanelTop size={15}/>上下</button>
              </div>
              <div className="add-window-grid">
                <button onClick={() => addWindow('instrument-list', 'detached')}><List size={16}/>添加固定列表</button>
                <button onClick={() => addWindow('instrument-list', 'attached')}><List size={16}/>添加联动列表</button>
                <button onClick={() => addWindow('chart', 'attached')}><BarChart3 size={16}/>添加联动图表</button>
                <button onClick={() => addWindow('chart', 'detached')}><BarChart3 size={16}/>添加固定图表</button>
              </div>
            </section>
            <WindowInspector
              windowState={focused}
              group={group}
              onUpdate={updateWindowProperties}
              onSetDriver={setDriver}
              onSwap={swapFocusedWindow}
              onRemove={removeWindow}
            />
          </aside>
        </div>
      </section>
    </main>
  )
}

function LayoutPreviewWindow({ windowId, group, onSelect }: {
  windowId: string
  group: WindowGroupState
  onSelect: (id: string) => void
}) {
  const item = group.windows.find(window => window.id === windowId)!
  return <button className={`layout-preview-window ${group.focusedWindowId === item.id ? 'active' : ''}`} onClick={() => onSelect(item.id)}>
    {item.type === 'chart' ? <BarChart3 size={20}/> : <List size={20}/>}<strong>{item.title}</strong>
    <small>{item.mode === 'attached' ? '联动' : '固定'} · {item.type === 'chart' ? '图表' : '列表'}</small>
  </button>
}

function WindowInspector({ windowState, group, onUpdate, onSetDriver, onSwap, onRemove }: {
  windowState: WorkspaceWindowState
  group: WindowGroupState
  onUpdate: (update: WorkspaceWindowState) => void
  onSetDriver: (targetId: string, sourceId: string, enabled: boolean) => void
  onSwap: () => void
  onRemove: () => void
}) {
  const drivers = group.windows.filter((item): item is InstrumentListWindowState =>
    item.type === 'instrument-list' && item.id !== windowState.id && (
      windowState.type === 'chart' || item.mode === 'detached'
    )
  )
  const selectedDrivers = new Set(group.attachments
    .filter(edge => edge.targetWindowId === windowState.id)
    .map(edge => edge.sourceWindowId))
  return <section className="window-inspector">
    <div className="panel-heading"><strong>窗口设置</strong><span>{windowState.id}</span></div>
    <label>窗口名称<input value={windowState.title} onChange={event => onUpdate({ ...windowState, title: event.target.value })}/></label>
    <label>窗口属性<select value={windowState.mode} onChange={event => {
      const mode = event.target.value as ChartWindowState['mode']
      onUpdate({ ...windowState, mode })
    }}><option value="attached">联动</option><option value="detached">固定</option></select></label>
    {windowState.mode === 'attached' && <fieldset className="driver-selector">
      <legend>驱动源</legend>
      {drivers.length === 0 && <span>暂无可选列表</span>}
      {drivers.map(driver => <label key={driver.id}>
        <input
          type="checkbox"
          checked={selectedDrivers.has(driver.id)}
          onChange={event => onSetDriver(windowState.id, driver.id, event.target.checked)}
        />
        <span>{driver.title}<small>{driver.mode === 'attached' ? '联动列表' : '固定列表'}</small></span>
      </label>)}
    </fieldset>}
    {windowState.type === 'chart' && <>
      <label>当前标的<input value={`${windowState.instrument.name} · ${windowState.instrument.symbol}`} disabled/></label>
    </>}
    <button className="command-button full" disabled={group.windows.length === 1} onClick={onSwap}><Copy size={15}/>与下一窗口交换</button>
    <button className="danger-button full" disabled={group.windows.length === 1} onClick={onRemove}><Trash2 size={15}/>删除窗口</button>
  </section>
}

function nextWindowTitle(windows: WorkspaceWindowState[]): string {
  const used = new Set(windows.map(item => item.title))
  let number = 1
  while (used.has(`表${number}`)) number += 1
  return `表${number}`
}

function resolveInstrument(windows: WorkspaceWindowState[]): Instrument {
  const chart = windows.find((item): item is ChartWindowState => item.type === 'chart')
  if (chart) return { ...chart.instrument }
  const list = windows.find((item): item is InstrumentListWindowState => item.type === 'instrument-list')
  return list?.content.instruments[0]
    ? { ...list.content.instruments[0] }
    : { symbol: 'BK1128.DC', name: 'CPO概念', kind: 'sector', exchange: 'DC', rows: 0 }
}
