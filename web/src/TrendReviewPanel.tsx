import { useEffect, useMemo, useState } from 'react'
import { Check, CheckCircle2, HelpCircle, LoaderCircle, Save, X, XCircle } from 'lucide-react'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import {
  createTrendReview,
  listTrendReviews,
  rebuildTrendReviewSnapshot,
  updateTrendReview,
  type TrendReview,
  type TrendReviewDecision,
  type TrendReviewLabel,
  type TrendReviewStatus,
} from './trendReviewClient'
import type { TrendTradingSystemSettings } from './tradingSystems'

type TrendReviewContext = { asOfDate: string; analysis: TrendAnalysisRun }

type TrendReviewPanelProps = {
  symbol: string
  name: string
  settings: TrendTradingSystemSettings
  currentAnalysis?: TrendAnalysisRun | null
  onContextChange: (value: TrendReviewContext | undefined) => void
  onClose: () => void
}

export function TrendReviewPanel({
  symbol,
  name,
  settings,
  currentAnalysis,
  onContextChange,
  onClose,
}: TrendReviewPanelProps) {
  const initialAsOf = currentAnalysis?.as_of_date ?? localDate()
  const [asOfDate, setAsOfDate] = useState(initialAsOf)
  const [intervalStart, setIntervalStart] = useState(() => shiftYear(initialAsOf, -1))
  const [intervalEnd, setIntervalEnd] = useState(initialAsOf)
  const [horizon, setHorizon] = useState<'short' | 'long'>('short')
  const [classification, setClassification] = useState<'positive' | 'near-miss' | 'ambiguous' | 'robustness'>('ambiguous')
  const [review, setReview] = useState<TrendReview>()
  const [labels, setLabels] = useState<TrendReviewLabel[]>([])
  const [rationale, setRationale] = useState('')
  const [recent, setRecent] = useState<TrendReview[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const counts = useMemo(() => decisionCounts(labels), [labels])
  const locked = review?.review_status === 'confirmed'

  useEffect(() => {
    let active = true
    void listTrendReviews(symbol).then(items => {
      if (active) setRecent(items)
    }).catch(() => undefined)
    return () => { active = false; onContextChange(undefined) }
  }, [symbol, onContextChange])

  const create = async () => {
    setBusy(true)
    setError(undefined)
    try {
      const result = await createTrendReview({
        symbol, horizon, intervalStart, intervalEnd, asOfDate,
        classification, tags: defaultTags(classification), rationale, settings,
      })
      setReview(result.review)
      setLabels(result.review.labels)
      setRationale(result.review.rationale)
      setRecent(items => [result.review, ...items])
      onContextChange({ asOfDate: result.review.as_of_date, analysis: result.analysis })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const reopen = async (reviewId: string) => {
    const selected = recent.find(item => item.review_id === reviewId)
    if (!selected) return
    setBusy(true)
    setError(undefined)
    try {
      const analysis = await rebuildTrendReviewSnapshot(reviewId)
      setReview(selected)
      setLabels(selected.labels)
      setRationale(selected.rationale)
      setAsOfDate(selected.as_of_date)
      setIntervalStart(selected.interval_start)
      setIntervalEnd(selected.interval_end)
      setHorizon(selected.horizon)
      setClassification(selected.classification as typeof classification)
      onContextChange({ asOfDate: selected.as_of_date, analysis })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const save = async (status: TrendReviewStatus) => {
    if (!review) return
    setBusy(true)
    setError(undefined)
    try {
      const updated = await updateTrendReview(review, status, labels, rationale)
      setReview(updated)
      setRecent(items => items.map(item => item.review_id === updated.review_id ? updated : item))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const decide = (itemId: string, decision: TrendReviewDecision) => {
    setLabels(items => items.map(item => item.item_id === itemId ? { ...item, decision } : item))
  }

  return (
    <div className="trend-review-panel" role="dialog" aria-label={`趋势分析人工复核 - ${name}`} onPointerDown={event => event.stopPropagation()}>
      <header>
        <span>趋势复核 · {name}</span>
        <button title="关闭复核" aria-label="关闭趋势复核" onClick={onClose}><X size={14}/></button>
      </header>
      <div className="trend-review-setup">
        <label>截止日期<input aria-label="复核截止日期" type="date" value={asOfDate} onChange={event => {
          setAsOfDate(event.target.value)
          setIntervalEnd(current => current > event.target.value ? event.target.value : current)
        }}/></label>
        <label>区间开始<input aria-label="复核区间开始" type="date" value={intervalStart} max={intervalEnd} onChange={event => setIntervalStart(event.target.value)}/></label>
        <label>区间结束<input aria-label="复核区间结束" type="date" value={intervalEnd} min={intervalStart} max={asOfDate} onChange={event => setIntervalEnd(event.target.value)}/></label>
        <label>级别<select aria-label="复核级别" value={horizon} onChange={event => setHorizon(event.target.value as 'short' | 'long')}><option value="short">短期</option><option value="long">长期</option></select></label>
        <label>类型<select aria-label="复核样本类型" value={classification} onChange={event => setClassification(event.target.value as typeof classification)}><option value="positive">正例</option><option value="near-miss">近似反例</option><option value="ambiguous">不确定</option><option value="robustness">边界稳健性</option></select></label>
        <button className="primary" disabled={busy} onClick={create}>{busy ? <LoaderCircle size={13}/> : <Check size={13}/>}加载截点</button>
      </div>
      {recent.length > 0 && <label className="trend-review-reopen">已有草稿<select aria-label="打开已有复核" value={review?.review_id ?? ''} onChange={event => void reopen(event.target.value)}><option value="">选择草稿</option>{recent.map(item => <option key={item.review_id} value={item.review_id}>{item.as_of_date} · {item.horizon === 'short' ? '短期' : '长期'} · {statusLabel(item.review_status)}</option>)}</select></label>}
      {error && <p className="trend-review-error">{error}</p>}
      {review && <>
        <div className="trend-review-summary">
          <span>截点 {review.as_of_date}</span><span>待定 {counts.pending}</span><span>接受 {counts.accepted}</span><span>拒绝 {counts.rejected}</span><span>不确定 {counts.ambiguous}</span><span>修订 {review.revision}</span>
        </div>
        <div className="trend-review-queue">
          {labels.map(label => <article key={label.item_id} className={`trend-review-candidate ${label.decision}`}>
            <div><span>{itemTypeLabel(label.item_type)}</span><small>{itemSummary(label)}</small></div>
            <div className="trend-review-decisions">
              <button disabled={locked} className={label.decision === 'accepted' ? 'active accept' : ''} title="接受" aria-label={`接受 ${label.item_id}`} onClick={() => decide(label.item_id, 'accepted')}><CheckCircle2 size={13}/></button>
              <button disabled={locked} className={label.decision === 'rejected' ? 'active reject' : ''} title="拒绝" aria-label={`拒绝 ${label.item_id}`} onClick={() => decide(label.item_id, 'rejected')}><XCircle size={13}/></button>
              <button disabled={locked} className={label.decision === 'ambiguous' ? 'active ambiguous' : ''} title="不确定" aria-label={`不确定 ${label.item_id}`} onClick={() => decide(label.item_id, 'ambiguous')}><HelpCircle size={13}/></button>
            </div>
          </article>)}
        </div>
        <textarea readOnly={locked} aria-label="复核说明" placeholder="记录接受、拒绝或不确定的原因" value={rationale} onChange={event => setRationale(event.target.value)}/>
        <footer>
          <span>{statusLabel(review.review_status)}</span>
          <button disabled={busy || locked} onClick={() => void save('ambiguous')}>标记不确定</button>
          <button disabled={busy || locked} onClick={() => void save('rejected')}>拒绝案例</button>
          <button disabled={busy || locked} onClick={() => void save('proposed')}><Save size={12}/>保存草稿</button>
          <button className="primary" disabled={busy || locked || counts.pending > 0 || counts.accepted + counts.rejected === 0} onClick={() => void save('confirmed')}>确认入库</button>
        </footer>
      </>}
    </div>
  )
}

function decisionCounts(labels: TrendReviewLabel[]): Record<TrendReviewDecision, number> {
  const result = { pending: 0, accepted: 0, rejected: 0, ambiguous: 0 }
  labels.forEach(item => { result[item.decision] += 1 })
  return result
}

function itemSummary(label: TrendReviewLabel): string {
  const payload = label.payload
  return String(payload.display_name ?? payload.event_kind ?? payload.kind ?? label.item_id)
}

function itemTypeLabel(value: string): string {
  return ({ anchor: '拐点', line: '趋势线', zone: '关键区', pattern: '形态', transition: '事件' } as Record<string, string>)[value] ?? value
}

function statusLabel(value: TrendReviewStatus): string {
  return ({ proposed: '草稿', ambiguous: '不确定', confirmed: '已确认', rejected: '已拒绝' })[value]
}

function defaultTags(value: string): string[] {
  if (value === 'robustness') return ['gap']
  if (value === 'near-miss') return ['pattern']
  return ['trend-line']
}

function localDate(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

function shiftYear(value: string, delta: number): string {
  const [year, month, day] = value.split('-').map(Number)
  const shifted = new Date(year + delta, month - 1, day)
  return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, '0')}-${String(shifted.getDate()).padStart(2, '0')}`
}
