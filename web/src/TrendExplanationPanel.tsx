import { Info, X } from 'lucide-react'
import { buildTrendExplanation } from './trendExplanation'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export function TrendExplanationPanel({
  run,
  onHighlightItemChange,
  onClose,
}: {
  run: TrendAnalysisRun
  onHighlightItemChange: (itemId?: string) => void
  onClose: () => void
}) {
  const explanation = buildTrendExplanation(run)
  if (!explanation) return null
  const clearHighlight = () => onHighlightItemChange(undefined)
  return (
    <aside className="trend-explanation-panel" role="dialog" aria-label="趋势分析结果说明">
      <header>
        <span><Info size={13}/>趋势分析说明</span>
        <button title="关闭结果说明" aria-label="关闭趋势分析结果说明" onClick={() => { clearHighlight(); onClose() }}><X size={13}/></button>
      </header>
      <div className="trend-explanation-meta">
        <span>{explanation.source === 'preview' ? '盘中预览' : '正式结果'}</span>
        <span>截至 {explanation.asOfDate}</span>
        {explanation.stale && <span className="stale">已过期</span>}
      </div>
      <p className="trend-explanation-summary">{explanation.summary}</p>
      <div className="trend-explanation-sections">
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
    </aside>
  )
}
