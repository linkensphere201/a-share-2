import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Play, Radar, RefreshCw } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import type { ThemeDefinition } from './themeStore'
import {
  listSignalDefinitions, listSignalItems, listSignalRuns, loadSignalRun,
  startSignalRun, type SignalChangeType, type SignalDefinition, type SignalItem,
  type SignalProfile, type SignalRun,
} from './signalReviewClient'

type Props = { theme: ThemeDefinition; onClose: () => void }
type ProfileFilter = 'all' | SignalProfile
type ChangeFilter = 'all' | SignalChangeType

const phaseLabels: Record<string, string> = {
  queued: '等待执行', memberships: '读取板块成分',
  'stock-features': '计算个股特征', 'board-ranking': '计算板块排名',
  completed: '已完成', failed: '失败',
}
const profileLabels: Record<SignalProfile, string> = { recent: '近期高权', historical: '历史高权' }
const changeLabels: Record<SignalChangeType, string> = { added: '新增', retained: '保留', removed: '移除' }

export function SignalReviewWorkspace({ theme, onClose }: Props) {
  const [definitions, setDefinitions] = useState<SignalDefinition[]>([])
  const [selectedDefinition, setSelectedDefinition] = useState<SignalDefinition>()
  const [runs, setRuns] = useState<SignalRun[]>([])
  const [selectedRun, setSelectedRun] = useState<SignalRun>()
  const [items, setItems] = useState<SignalItem[]>([])
  const [selectedItem, setSelectedItem] = useState<SignalItem>()
  const [profile, setProfile] = useState<ProfileFilter>('all')
  const [change, setChange] = useState<ChangeFilter>('all')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    listSignalDefinitions(controller.signal).then(value => {
      setDefinitions(value)
      setSelectedDefinition(current => current ?? value[0])
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedDefinition) return
    const controller = new AbortController()
    listSignalRuns(selectedDefinition.signal_id, controller.signal).then(value => {
      setRuns(value)
      setSelectedRun(current => value.find(item => item.run_id === current?.run_id) ?? value[0])
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedDefinition])

  useEffect(() => {
    if (!selectedRun) { setItems([]); setSelectedItem(undefined); return }
    const controller = new AbortController()
    listSignalItems(selectedRun.run_id, controller.signal).then(value => {
      setItems(value)
      setSelectedItem(current => value.find(item => item.item_id === current?.item_id))
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedRun?.run_id, selectedRun?.status])

  useEffect(() => {
    if (selectedRun?.status !== 'running' || !selectedDefinition) return
    const timer = window.setInterval(async () => {
      try {
        const next = await loadSignalRun(selectedRun.run_id)
        setSelectedRun(next)
        setRuns(await listSignalRuns(selectedDefinition.signal_id))
      } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [selectedDefinition, selectedRun?.run_id, selectedRun?.status])

  const filtered = useMemo(() => items.filter(item =>
    (profile === 'all' || item.profile === profile)
    && (change === 'all' || item.change_type === change),
  ), [items, profile, change])
  useEffect(() => {
    if (filtered.some(item => item.item_id === selectedItem?.item_id)) return
    setSelectedItem(undefined)
  }, [filtered, selectedItem?.item_id])
  const counts = useMemo(() => ({
    recent: items.filter(item => item.active && item.profile === 'recent').length,
    historical: items.filter(item => item.active && item.profile === 'historical').length,
  }), [items])
  const progress = selectedRun?.work_total
    ? Math.round(selectedRun.work_done / selectedRun.work_total * 100) : 0

  const run = async () => {
    if (!selectedDefinition) return
    try {
      setError('')
      const next = await startSignalRun(selectedDefinition.signal_id)
      setRuns(current => [next, ...current])
      setSelectedRun(next)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  return <main className="signal-workspace">
    <header className="signal-toolbar">
      <button className="icon-button" title="返回工作台" aria-label="返回工作台" onClick={onClose}><ArrowLeft size={16}/></button>
      <span className="signal-title"><Radar size={17}/>信号复盘 <small>手工触发 · 结果可追溯</small></span>
      <select aria-label="选择信号" value={selectedDefinition?.signal_id ?? ''} onChange={event => {
        setSelectedDefinition(definitions.find(item => item.signal_id === event.target.value))
        setSelectedRun(undefined)
      }}>{definitions.map(item => <option key={item.signal_id} value={item.signal_id}>{item.name}</option>)}</select>
      {selectedDefinition && <span className="signal-description">{selectedDefinition.description}</span>}
      <button className="primary-button" disabled={!selectedDefinition || runs.some(item => item.status === 'running')} onClick={() => void run()}>
        {runs.some(item => item.status === 'running') ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}运行本期信号
      </button>
    </header>
    {error && <button className="signal-error" onClick={() => setError('')}>{error}</button>}
    <section className={selectedItem ? 'signal-grid inspector-open' : 'signal-grid'}>
      <aside className="signal-runs">
        <header><span>历史轮次</span><small>{runs.length}</small></header>
        <div className="signal-scroll">{runs.length === 0 && <div className="signal-empty compact">尚未运行</div>}{runs.map(item => <button key={item.run_id} className={selectedRun?.run_id === item.run_id ? 'active' : ''} onClick={() => setSelectedRun(item)}>
          <span>{item.effective_date} <i>R{item.revision}</i></span>
          <small>{item.status === 'running' ? `${phaseLabels[item.phase] ?? item.phase} ${progress}%` : item.status === 'failed' ? '执行失败' : `${item.item_count} 项`}</small>
          <em className={item.status}/>
        </button>)}</div>
      </aside>
      <section className="signal-results">
        <header><span>复盘结果</span><small>{selectedRun ? `+${selectedRun.added_count} =${selectedRun.retained_count} -${selectedRun.removed_count}` : '请选择轮次'}</small></header>
        {selectedRun?.status === 'running' && <div className="signal-progress"><i style={{ width: `${progress}%` }}/></div>}
        <div className="signal-filters">
          {(['all', 'recent', 'historical'] as ProfileFilter[]).map(value => <button key={value} className={profile === value ? 'active' : ''} onClick={() => setProfile(value)}>{value === 'all' ? '全部' : profileLabels[value]} {value === 'recent' ? counts.recent : value === 'historical' ? counts.historical : items.length}</button>)}
        </div>
        <div className="signal-filters secondary">
          {(['all', 'added', 'retained', 'removed'] as ChangeFilter[]).map(value => <button key={value} className={change === value ? 'active' : ''} onClick={() => setChange(value)}>{value === 'all' ? '全部变化' : changeLabels[value]}</button>)}
        </div>
        <div className="signal-result-head"><span>#</span><span>标的</span><span>板块</span><span>变化</span></div>
        <div className="signal-scroll">{selectedRun?.status === 'failed' && <div className="signal-empty compact error">{selectedRun.error}</div>}{filtered.map(item => <button key={item.item_id} className={`${selectedItem?.item_id === item.item_id ? 'active ' : ''}${item.active ? '' : 'inactive'}`} onClick={() => setSelectedItem(item)}>
          <span>{item.rank}</span><span><span className="instrument-name-line"><b>{item.name}</b><MarketBoardBadge instrument={item}/></span><small>{item.symbol}</small></span><span>{item.payload.board_count ?? 0}<small>{profileLabels[item.profile]}</small></span><span className={`change ${item.change_type}`}>{changeLabels[item.change_type]}</span>
        </button>)}</div>
      </section>
      {selectedItem && <section className="signal-inspector">
        <header>{selectedItem ? <><span className="instrument-name-line"><span>{selectedItem.name}</span><MarketBoardBadge instrument={selectedItem}/></span><small>{profileLabels[selectedItem.profile]} · 得分 {(selectedItem.score * 100).toFixed(1)} · 置信 {(selectedItem.confidence * 100).toFixed(1)}</small></> : <span>证据与 K 线</span>}</header>
        <div className="signal-chart">{selectedItem
          ? <ChartCanvas key={`${selectedRun?.run_id}:${selectedItem.symbol}`} symbol={selectedItem.symbol} instrumentName={selectedItem.name} instrumentKind={selectedItem.kind} focused theme={theme} range="1Y" priceMode="normal" volumeVisible indicator="none" settlementVisible={false} openInterestVisible={false} asOfDate={selectedRun?.effective_date}/>
          : <div className="signal-empty">选择一项结果查看 K 线</div>}</div>
        <div className="signal-evidence"><header><span>引用证据</span><small>{selectedItem?.evidence.length ?? 0}</small></header>
          <div className="signal-scroll">{selectedItem?.evidence.map(evidence => <button key={evidence.evidence_id} title="该证据为板块排名证据；形态几何证据将在对应信号中联动高亮">
            <code>[{evidence.alias}]</code><span>{evidence.payload.board_name ?? evidence.evidence_type}<small>{evidence.payload.board_classification === 'industry' ? '行业板块' : '概念板块'} · 板块第 {evidence.payload.rank ?? '-'} · 得分 {((evidence.payload.score ?? 0) * 100).toFixed(1)}</small></span>
          </button>)}</div>
        </div>
      </section>}
    </section>
  </main>
}
