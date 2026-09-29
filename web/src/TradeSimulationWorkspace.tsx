import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Download, FlaskConical, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { loadSimulationStrategies, runTradeSimulation, type SimulationInput, type SimulationResult, type SimulationSession, type SimulationStrategy } from './tradeSimulationClient'
import './tradeSimulation.css'

const blankSession = (): SimulationSession => ({ session: '', open: 0, close: null, atr20: null,
  lower_limit: 0, upper_limit: 0, tradable: true, verified: true, market_risk_off: false })
const initial: SimulationInput = { strategy_id: 'trend-trade-v1', label: '手工情景', signal_session: '', capital: 100000, upper: 0, lower: 0,
  signal_close: 0, atr20: 0, target: 0, median_amount20: 100000000, lot_size: 100, fee_bps: 10,
  slippage_bps: 5, eligibility_assumed: false, sessions: [blankSession(), blankSession()] }
const statuses: Record<string, string> = { held: '持仓中', 'exit-pending': '待退出', closed: '已平仓', expired: '入场过期', 'not-entered': '未开仓' }
const labels: Record<string, string> = {
  entry: '买入成交', exit: '卖出成交', rejected: '不合格', expired: '放弃开仓', deferred: '延迟成交',
  'exit-signal': '退出触发', 'stop-raised': '保护线上移', 'data-warning': '数据缺失',
  'eligibility-not-assumed': '资格假设未确认', liquidity: '流动性不足', 'insufficient-capacity': '资金或风险额度不足',
  'opening-unavailable-or-outside-plan': '开盘不可成交或超出价格范围', 'next-open': '次日开盘',
  'exit-not-executable': '当前开盘不能卖出', 'structural-stop': '结构止损', 'trailing-stop': '跟踪止损',
  target: '目标退出', 'market-risk': '市场风险退出', 'account-risk': '账户风险退出',
  'missing-close': '收盘缺失，沿用上次估值', 'next-session-stop': '下一会话生效',
}
const numberFields = [
  ['capital', '初始资金'], ['upper', '箱顶'], ['lower', '箱底'], ['signal_close', '信号收盘'],
  ['atr20', '信号 ATR20'], ['target', '阻力目标'], ['median_amount20', '前20日成交额中位数'],
  ['lot_size', '交易单位（股）'], ['fee_bps', '单边费用（bp）'], ['slippage_bps', '单边滑点（bp）'],
] as const
const format = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })

export function TradeSimulationWorkspace({ onClose }: { onClose: () => void }) {
  const [input, setInput] = useState<SimulationInput>(initial)
  const [strategies, setStrategies] = useState<SimulationStrategy[]>([])
  const strategy = strategies.find(item => item.strategy_id === input.strategy_id)
  const examples = strategy?.scenarios ?? []
  const [reload, setReload] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<SimulationResult[]>([])
  const [selected, setSelected] = useState<SimulationResult>()
  const [tab, setTab] = useState<'events' | 'equity'>('events')
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    loadSimulationStrategies(controller.signal).then(value => {
      setStrategies(value.items)
      setInput(current => value.items.some(item => item.strategy_id === current.strategy_id) || !value.items.length
        ? current : { ...initial, strategy_id: value.items[0].strategy_id })
    }).catch(reason => {
      if (!controller.signal.aborted) setError(String(reason.message ?? reason))
    })
    return () => controller.abort()
  }, [reload])
  useEffect(() => () => request.current?.abort(), [])

  function changeSession(index: number, patch: Partial<SimulationSession>) {
    setInput(current => ({ ...current, sessions: current.sessions.map((row, i) => i === index ? { ...row, ...patch } : row) }))
  }
  async function run() {
    if (request.current || !strategy) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true); setError('')
    try {
      const value = await runTradeSimulation(input, controller.signal)
      if (!controller.signal.aborted) {
        setSelected(value)
        setResults(current => [value, ...current.filter(item => item.input_digest !== value.input_digest)].slice(0, 10))
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
      request.current = null
    }
  }
  function download() {
    if (!selected) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(selected, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${selected.strategy_id}-${selected.input_digest.slice(0, 12)}.json`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <main className="trade-lab">
    <header className="trade-lab-toolbar">
      <button title="返回主界面" aria-label="返回主界面" onClick={onClose}><ArrowLeft size={17}/></button>
      <h1><FlaskConical size={19}/>模拟测试</h1><span>{strategy?.name ?? '策略未就绪'}</span>
      <strong className="trade-lab-badge">手工情景 · 非历史回测</strong>
      <button title="导出当前结果" aria-label="导出当前结果" disabled={!selected || busy} onClick={download}><Download size={17}/></button>
    </header>
    <div className="trade-lab-body">
      <aside className="trade-lab-settings">
        <label>执行策略<select aria-label="执行策略" value={strategy ? input.strategy_id : ''} disabled={busy || !strategies.length} onChange={e => {
          setInput({ ...initial, strategy_id: e.target.value, sessions: [blankSession(), blankSession()] })
          setSelected(undefined); setError('')
        }}>{!strategies.length && <option value="">暂无可用策略</option>}{strategies.map(item => <option key={item.strategy_id} value={item.strategy_id}>{item.name}（{item.version}）</option>)}</select></label>
        <h2>冻结交易计划</h2>
        <div className="trade-lab-examples"><select aria-label="载入合成测试情景" disabled={busy} value="" onChange={e => {
          const value = examples[Number(e.target.value)]
          if (value) setInput(structuredClone(value))
        }}><option value="">载入合成测试情景</option>{examples.map((item, index) => <option key={index} value={index}>{item.label}</option>)}</select>
          <button title="刷新测试情景" aria-label="刷新测试情景" disabled={busy} onClick={() => setReload(x => x + 1)}><RefreshCw size={15}/></button></div>
        <form onSubmit={e => { e.preventDefault(); void run() }}>
          <fieldset disabled={busy}>
            <label>情景名称<input required maxLength={80} value={input.label} onChange={e => setInput({ ...input, label: e.target.value })}/></label>
            <label>信号日期<input required type="date" value={input.signal_session} onChange={e => setInput({ ...input, signal_session: e.target.value })}/></label>
            <div className="trade-lab-input-grid">{numberFields.map(([key, label]) => <label key={key}>{label}<input required type="number" min={key.includes('bps') ? 0 : .000001} step="any" value={input[key]} onChange={e => setInput({ ...input, [key]: Number(e.target.value) })}/></label>)}</div>
            <label className="trade-lab-check"><input type="checkbox" checked={input.eligibility_assumed} onChange={e => setInput({ ...input, eligibility_assumed: e.target.checked })}/>假设市场、板块和个股资格已通过</label>
          </fieldset>
          <button className="trade-lab-run" type="submit" disabled={busy || !strategy || input.sessions.some(row => !row.session)}><Play size={16}/>{busy ? '模拟中…' : '运行模拟'}</button>
        </form>
        <p className="trade-lab-warning">未核验真实形态、历史交易日和资格；不含复权、组合调度及账户暂停。结果不代表策略盈利能力。</p>
        <h2>本次会话记录</h2>
        <nav className="trade-lab-history" aria-label="模拟记录">{results.length ? results.map(result => <button key={result.input_digest} className={selected === result ? 'active' : ''} onClick={() => setSelected(result)}>
          <span>{result.input.label}</span><small>{result.strategy_name} · {statuses[result.status]} · {(result.summary.net_return * 100).toFixed(2)}%</small>
        </button>) : <span className="trade-lab-muted">暂无记录</span>}</nav>
      </aside>
      <section className="trade-lab-main">
        {error && <div role="alert" className="trade-lab-error">{error}</div>}
        <section className="trade-lab-sessions">
          <header><h2>逐日情景</h2><span>{input.sessions.length} / 250 会话</span><button title="增加会话" aria-label="增加会话" disabled={busy || input.sessions.length >= 250} onClick={() => setInput({ ...input, sessions: [...input.sessions, blankSession()] })}><Plus size={16}/></button></header>
          <div className="trade-lab-table-scroll"><table><thead><tr><th>日期</th><th>开盘</th><th>收盘</th><th>ATR20</th><th>跌停价</th><th>涨停价</th><th>可交易</th><th>开盘证据</th><th>风险退出</th><th/></tr></thead>
            <tbody>{input.sessions.map((row, index) => <tr key={index}>
              <td><input aria-label={`日期 ${index + 1}`} disabled={busy} type="date" value={row.session} onChange={e => changeSession(index, { session: e.target.value })}/></td>
              {(['open', 'close', 'atr20', 'lower_limit', 'upper_limit'] as const).map(key => <td key={key}><input aria-label={`${key} ${index + 1}`} disabled={busy} type="number" min="0" step="any" value={row[key] ?? ''} onChange={e => changeSession(index, { [key]: e.target.value === '' && (key === 'close' || key === 'atr20') ? null : Number(e.target.value) })}/></td>)}
              {(['tradable', 'verified', 'market_risk_off'] as const).map(key => <td key={key}><input aria-label={`${key} ${index + 1}`} disabled={busy} type="checkbox" checked={row[key]} onChange={e => changeSession(index, { [key]: e.target.checked })}/></td>)}
              <td><button aria-label={`移除会话 ${index + 1}`} title="移除会话" disabled={busy || input.sessions.length <= 2} onClick={() => setInput({ ...input, sessions: input.sessions.filter((_, i) => i !== index) })}><Trash2 size={14}/></button></td>
            </tr>)}</tbody></table></div>
        </section>
        {selected ? <section className="trade-lab-result" aria-label="模拟结果">
          <header><h2>{selected.input.label}</h2><span>{statuses[selected.status]}</span><span>{selected.strategy_name} · {selected.strategy_version}</span><small>{selected.input_digest.slice(0, 12)}</small></header>
          <div className="trade-lab-metrics">
            <div><span>账户净变化</span><strong>{(selected.summary.net_return * 100).toFixed(2)}%</strong></div>
            <div><span>最大回撤</span><strong>{(selected.summary.max_drawdown * 100).toFixed(2)}%</strong></div>
            <div><span>已实现盈亏</span><strong>{format(selected.summary.realized_pnl)}</strong></div>
            <div><span>期末持股</span><strong>{selected.summary.open_quantity} 股</strong></div>
          </div>
          {selected.summary.has_stale_valuation && <p role="status" className="trade-lab-warning">存在过期估值，区间收益与回撤证据不完整。</p>}
          <div className="trade-lab-plan"><span>入场上限 {format(selected.plan.ceiling)}</span><span>初始止损 {format(selected.plan.stop)}</span><span>目标 {format(selected.plan.target)}</span><span>计划 {selected.planned_quantity} 股</span><span>费用 {format(selected.summary.fees)}</span></div>
          <EquityChart result={selected}/>
          <div className="trade-lab-tabs" role="tablist" aria-label="结果视图"><button role="tab" aria-selected={tab === 'events'} onClick={() => setTab('events')}>成交与事件</button><button role="tab" aria-selected={tab === 'equity'} onClick={() => setTab('equity')}>逐日权益</button></div>
          <div className="trade-lab-table-scroll" role="tabpanel"><table>{tab === 'events' ? <><thead><tr><th>日期</th><th>事件</th><th>原因</th><th>价格</th><th>股数</th></tr></thead><tbody>{selected.events.map((event, i) => <tr key={i}><td>{event.session}</td><td>{labels[event.kind] ?? event.kind}</td><td>{labels[event.reason] ?? event.reason}</td><td>{format(event.price)}</td><td>{format(event.quantity)}</td></tr>)}</tbody></> : <><thead><tr><th>日期</th><th>权益</th><th>现金</th><th>股数</th><th>下期保护线</th><th>估值</th></tr></thead><tbody>{selected.equity.map(row => <tr key={row.session}><td>{row.session}</td><td>{format(row.equity)}</td><td>{format(row.cash)}</td><td>{row.quantity}</td><td>{format(row.stop)}</td><td>{row.stale ? '过期' : '有效'}</td></tr>)}</tbody></>}</table></div>
        </section> : <section className="trade-lab-empty"><FlaskConical size={36}/><h2>尚无模拟结果</h2><span>历史回测：数据适配与组合回放未接入</span></section>}
      </section>
    </div>
  </main>
}

function EquityChart({ result }: { result: SimulationResult }) {
  const values = [result.input.capital, ...result.equity.map(row => row.equity)]
  const low = Math.min(...values), high = Math.max(...values), span = high - low || 1
  const points = values.map((value, i) => `${20 + i * 660 / (values.length - 1)},${130 - (value - low) / span * 110}`).join(' ')
  return <figure className="trade-lab-chart"><figcaption><span>模拟账户权益</span><span>{format(low)} — {format(high)}</span></figcaption>
    <svg viewBox="0 0 700 150" role="img" aria-label="模拟账户权益曲线" preserveAspectRatio="none"><path d="M20 20H680 M20 75H680 M20 130H680" className="trade-lab-grid"/><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke"/></svg>
  </figure>
}
