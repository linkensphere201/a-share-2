import { Info, X } from 'lucide-react'
import { readGeneratedBreakoutState } from './generatedAnalysisProjection'
import { buildTrendExplanation } from './trendExplanation'
import { readTrendEvidence } from './trendEvidence'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export function TrendExplanationPanel({
  run,
  onHighlightItemChange,
  onClose,
  embedded = false,
}: {
  run: TrendAnalysisRun
  onHighlightItemChange: (itemId?: string) => void
  onClose: () => void
  embedded?: boolean
}) {
  const explanation = buildTrendExplanation(run)
  if (!explanation) return null
  const breakoutState = readGeneratedBreakoutState(run, true)
  const evidence = readTrendEvidence(run, breakoutState)
  const clearHighlight = () => onHighlightItemChange(undefined)
  const content = <>
      {!embedded && <header>
        <span><Info size={13}/>趋势分析说明</span>
        <button title="关闭结果说明" aria-label="关闭趋势分析结果说明" onClick={() => { clearHighlight(); onClose() }}><X size={13}/></button>
      </header>}
      <div className="trend-explanation-meta">
        <span>{explanation.source === 'preview' ? '盘中预览' : '正式结果'}</span>
        <span>截至 {explanation.asOfDate}</span>
        {explanation.stale && <span className="stale">已过期</span>}
      </div>
      <p className="trend-explanation-summary">{explanation.summary}</p>
      <div className="trend-explanation-sections">
        {evidence && <section className="trend-explanation-evidence">
          <h3>分析证据</h3>
          <dl>
            {evidence.observedAt && <><dt>观测时间</dt><dd>{evidence.observedAt}</dd></>}
            <dt>结果</dt><dd>{evidence.source === 'preview' ? '盘中预览' : '正式'} · {evidence.asOfDate} · {evidence.timeframe}</dd>
            <dt>状态</dt><dd>{breakoutState ? evidenceStateLabel(breakoutState.eventKind, breakoutState.state) : '暂无结构事件'}{evidence.stale ? ' · 已过期' : ''}</dd>
            {evidence.patternName && <><dt>主形态</dt><dd>{evidence.patternName}{evidence.score !== undefined ? ` · ${Math.round(evidence.score * 100)}分` : ''}</dd></>}
            {evidence.boundaryPrice !== undefined && <><dt>边界</dt><dd>{evidence.boundaryPrice.toFixed(2)}</dd></>}
            {evidence.invalidationPrice !== undefined && <><dt>失效位</dt><dd>{evidence.invalidationPrice.toFixed(2)}</dd></>}
            {evidence.context && <><dt>环境</dt><dd>{contextLabel(evidence.context.alignment)} · {Math.round(evidence.context.score * 100)}分 · {evidence.context.availableCount}项</dd></>}
          </dl>
          {evidence.scoreComponents.length > 0 && <div className="trend-evidence-group">
            <span>置信分解</span>
            <div>{evidence.scoreComponents.map(item => <i key={item.label}>{item.label} {Math.round(item.value * 100)}</i>)}</div>
          </div>}
          {evidence.observation.length > 0 && <div className="trend-evidence-group">
            <span>量价观测</span>
            <div>{evidence.observation.map(item => <i key={item.label}>{item.label} {item.value}</i>)}</div>
          </div>}
        </section>}
        {explanation.sections.map(section => section.items.length > 0 && <section key={section.id}>
          <h3>{section.title}<small>{section.items.length}</small></h3>
          {section.items.map(item => <article
            key={item.analysisItemId}
            tabIndex={0}
            data-analysis-item-id={item.analysisItemId}
            onPointerEnter={() => onHighlightItemChange(item.analysisItemId)}
            onPointerLeave={clearHighlight}
            onFocus={() => onHighlightItemChange(item.analysisItemId)}
            onBlur={clearHighlight}
          >
            <div><span>{item.title}</span>{item.score !== undefined && <small>{Math.round(item.score * 100)}分</small>}</div>
            <p>{item.detail}</p>
          </article>)}
        </section>)}
        {explanation.warnings.length > 0 && <section className="warnings">
          <h3>数据提示</h3>
          {explanation.warnings.map(value => <p key={value}>{value}</p>)}
        </section>}
      </div>
    </>
  return embedded
    ? <section className="trend-explanation-pane" aria-label="形态分析结果">{content}</section>
    : <aside className="trend-explanation-panel" role="dialog" aria-label="趋势分析结果说明">{content}</aside>
}

function evidenceStateLabel(eventKind: string | undefined, state: string): string {
  if (eventKind === 'upward-breakout') return '向上突破'
  if (eventKind === 'downward-breakdown') return '向下破位'
  if (eventKind === 'retest') return '回踩确认'
  if (eventKind === 'false-breakout-risk') return '假突破风险'
  if (eventKind === 'no-structural-change') return '无结构变化'
  return ({
    forming: '形成中', ready: '准备', triggered: '已触发', confirmed: '已确认',
    retesting: '回踩中', continuing: '延续', failed: '失败',
    invalidated: '失效', stale: '已过期',
  } as Record<string, string>)[state] ?? state
}

function contextLabel(value: string): string {
  if (value === 'supportive') return '顺风'
  if (value === 'adverse') return '逆风'
  return '混合'
}
