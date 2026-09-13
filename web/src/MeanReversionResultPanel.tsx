import type { SignalScoreResult } from './signalReviewClient'

export function MeanReversionResultPanel({
  score,
  highlightedProjectionId,
  onHighlight,
}: {
  score: SignalScoreResult
  highlightedProjectionId?: string
  onHighlight: (projectionId?: string) => void
}) {
  const conclusion = score.conclusion
  const opportunity = score.opportunity
  return <div className="mean-reversion-result">
    <header>
      <span className={`score-grade grade-${score.grade.toLowerCase()}`}>{score.total_score.toFixed(1)}</span>
      <span>{meanFamilyLabel(score.setup_family)}<small>{meanStateLabel(opportunity?.state)}</small></span>
      <span className={score.eligible ? 'eligible' : 'waiting'}>{score.eligible ? '机会确认' : '观察/否决'}</span>
    </header>
    <p>{score.summary}</p>
    <div className="mean-reversion-sections">
      {conclusion?.sections.map(section => {
        const projectionId = section.evidence_refs.find(reference => (
          score.chart_projection?.some(projection => projection.projection_id === reference)
        ))
        return <button
          key={section.code}
          className={projectionId && projectionId === highlightedProjectionId ? 'active' : ''}
          onPointerEnter={() => onHighlight(projectionId)}
          onPointerLeave={() => onHighlight(undefined)}
          onFocus={() => onHighlight(projectionId)}
          onBlur={() => onHighlight(undefined)}
        >
          <span>{section.title}</span><small>{section.text}</small>
        </button>
      })}
    </div>
    {opportunity && <div className="mean-reversion-execution">
      <span>确认<small>{price(opportunity.confirmation_price)}</small></span>
      <span>失效<small>{price(opportunity.invalidation_price)}</small></span>
      <span>压力盈亏比<small>{ratio(opportunity.stressed_risk_reward)}</small></span>
      <span>最长持有<small>{opportunity.maximum_holding_sessions ?? '-'}日</small></span>
    </div>}
    <div className="mean-reversion-targets">
      {opportunity?.targets.map(target => <button
        key={target.label}
        onPointerEnter={() => onHighlight(`mr:target:${target.label}`)}
        onPointerLeave={() => onHighlight(undefined)}
        onFocus={() => onHighlight(`mr:target:${target.label}`)}
        onBlur={() => onHighlight(undefined)}
      >{target.label}<small>{price(target.price)} · 压力 {ratio(target.stressed_risk_reward_ratio)}</small></button>)}
    </div>
    {!score.eligible && <footer>{score.risk_summary}</footer>}
  </div>
}

function meanFamilyLabel(value?: string) {
  return value === 'directional-pullback' ? '趋势内次级折返'
    : value === 'oversold-exhaustion' ? '超跌衰竭' : '无均值结构'
}

function meanStateLabel(value?: string) {
  return {
    'reversal-confirmed': '反转已确认', 'exhaustion-watch': '衰竭观察',
    'extreme-pending': '极端偏离', 'deviation-building': '偏离扩大',
    'structural-break': '结构破坏', 'stable-center': '中心附近',
  }[value ?? ''] ?? value ?? '-'
}

function price(value?: number | null) {
  return typeof value === 'number' ? value.toFixed(2) : '-'
}

function ratio(value?: number | null) {
  return typeof value === 'number' ? `${value.toFixed(2)}:1` : '-'
}
