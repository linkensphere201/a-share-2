import { useEffect, useState } from 'react'
import { BarChart3, X } from 'lucide-react'

type Summary = {
  symbol: string
  status: string
  algorithm_version?: string
  smoothing_period?: number
  last_trade_date?: string | null
  latest_absolute_close?: number | null
  latest_coverage_ratio?: number | null
  latest_change_percent?: number | null
}

type Contributor = {
  direction: 'positive' | 'negative'
  rank: number
  symbol: string
  name: string
  active_close: number
  change_contribution: number
}

type Comparator = {
  symbol: string
  name: string
  change_percent?: number | null
  divergence_percent_points?: number | null
}

type Diagnostics = {
  status: string
  trade_date?: string
  eligible_count?: number
  total_count?: number
  input_digest?: string
  contributors: Contributor[]
  comparators: Comparator[]
}

export function ActiveMarketValueReadout({ symbol }: { symbol: string }) {
  const [summary, setSummary] = useState<Summary>()
  const [open, setOpen] = useState(false)
  const [diagnostics, setDiagnostics] = useState<Diagnostics>()
  useEffect(() => {
    if (symbol !== 'SHAMV.A') {
      setSummary(undefined)
      setOpen(false)
      setDiagnostics(undefined)
      return
    }
    const controller = new AbortController()
    fetch('/api/active-market-value', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<Summary>
      })
      .then(setSummary)
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setSummary(undefined)
      })
    return () => controller.abort()
  }, [symbol])
  useEffect(() => {
    if (!open || symbol !== 'SHAMV.A') return
    const controller = new AbortController()
    fetch('/api/active-market-value/diagnostics/latest', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<Diagnostics>
      })
      .then(setDiagnostics)
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setDiagnostics(undefined)
      })
    return () => controller.abort()
  }, [open, symbol])
  if (!summary || summary.status !== 'ready') return null
  const positive = diagnostics?.contributors.filter(item => item.direction === 'positive') ?? []
  const negative = diagnostics?.contributors.filter(item => item.direction === 'negative') ?? []
  return <span className="amv-readout-wrap">
    <button
      className="amv-title-readout"
      title={`算法 ${summary.algorithm_version ?? '-'} · 打开活跃市值诊断`}
      aria-label="打开活跃市值诊断"
      aria-expanded={open}
      onClick={() => setOpen(value => !value)}
    >
      <i>截至 {summary.last_trade_date?.slice(5) ?? '-'}</i>
      <i>活跃 {formatValue(summary.latest_absolute_close)}</i>
      <i className={(summary.latest_change_percent ?? 0) >= 0 ? 'rise' : 'fall'}>{formatChange(summary.latest_change_percent)}</i>
      <i>覆盖 {formatCoverage(summary.latest_coverage_ratio)}</i>
      <i>CYF{summary.smoothing_period ?? 13}</i>
      <BarChart3 size={12}/>
    </button>
    {open && <aside className="amv-diagnostics" role="dialog" aria-label="活跃市值诊断">
      <header><span>活跃市值诊断</span><button aria-label="关闭活跃市值诊断" onClick={() => setOpen(false)}><X size={13}/></button></header>
      {!diagnostics && <div className="amv-diagnostics-loading">正在读取物化证据...</div>}
      {diagnostics && <>
        <div className="amv-diagnostics-metrics">
          <span><small>数据日期</small>{diagnostics.trade_date ?? '-'}</span>
          <span><small>参与股票</small>{diagnostics.eligible_count ?? 0} / {diagnostics.total_count ?? 0}</span>
          <span><small>覆盖率</small>{formatCoverage(summary.latest_coverage_ratio)}</span>
          <span title={diagnostics.input_digest}><small>输入摘要</small>{diagnostics.input_digest?.slice(0, 10) ?? '-'}</span>
        </div>
        <div className="amv-contributor-columns">
          <ContributorList title="正贡献" items={positive}/>
          <ContributorList title="负贡献" items={negative}/>
        </div>
        <div className="amv-comparators">
          <strong>相对宽基</strong>
          {diagnostics.comparators.length === 0 && <span className="empty">同日宽基数据不可用</span>}
          {diagnostics.comparators.map(item => <span key={item.symbol}>
            <em>{item.name}</em>
            <i className={(item.divergence_percent_points ?? 0) >= 0 ? 'rise' : 'fall'}>{formatSigned(item.divergence_percent_points)} pct</i>
          </span>)}
        </div>
        <footer>独立 CYF 类模型估值，仅表示近期换手对应的活跃流通市值，不代表市场净流入；OHLC 为跨成分合成包络。</footer>
      </>}
    </aside>}
  </span>
}

function ContributorList({ title, items }: { title: string; items: Contributor[] }) {
  return <section><strong>{title}</strong>{items.slice(0, 5).map(item => <span key={item.symbol}>
    <em title={`${item.name} ${item.symbol}`}>{item.name}</em>
    <i className={item.direction === 'positive' ? 'rise' : 'fall'}>{formatContribution(item.change_contribution)}</i>
  </span>)}{items.length === 0 && <span className="empty">暂无回算数据</span>}</section>
}

function formatValue(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  if (value >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(2)}万亿`
  return `${(value / 100_000_000).toFixed(0)}亿`
}

function formatCoverage(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function formatChange(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatSigned(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

function formatContribution(value: number): string {
  const amount = Math.abs(value)
  const prefix = value >= 0 ? '+' : '-'
  if (amount >= 1_000_000_000_000) return `${prefix}${(amount / 1_000_000_000_000).toFixed(2)}万亿`
  return `${prefix}${(amount / 100_000_000).toFixed(0)}亿`
}
