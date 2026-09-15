import { useState, type MouseEvent } from 'react'
import type { SignalScoreResult } from './signalReviewClient'
import { MarketBoardBadge } from './MarketBoardBadge'

export function ParallelReviewLeaders({ trend, mean, selected, onSelect, onContextMenu }: {
  trend: SignalScoreResult[]; mean: SignalScoreResult[]; selected?: SignalScoreResult
  onSelect: (score: SignalScoreResult) => void
  onContextMenu?: (event: MouseEvent, score: SignalScoreResult) => void
}) {
  return <div className="parallel-review-leaders">
    <LeaderColumn title="趋势体系前排" scores={trend} selected={selected} onSelect={onSelect} onContextMenu={onContextMenu}/>
    <LeaderColumn title="均值回归前排" scores={mean} selected={selected} onSelect={onSelect} onContextMenu={onContextMenu}/>
  </div>
}

function LeaderColumn({ title, scores, selected, onSelect, onContextMenu }: {
  title: string; scores: SignalScoreResult[]; selected?: SignalScoreResult
  onSelect: (score: SignalScoreResult) => void
  onContextMenu?: (event: MouseEvent, score: SignalScoreResult) => void
}) {
  const [limit, setLimit] = useState(10)
  return <section aria-label={title}>
    <header>{title}<small>{scores.length} 项</small></header>
    <div className="parallel-review-rows">
      {!scores.length && <p>本轮暂无符合条件的结果；旧轮次可能尚未计算该体系。</p>}
      {scores.slice(0, limit).map((score, index) => <button
        key={`${score.entity_scope}:${score.entity_key}`}
        className={selected?.system_id === score.system_id && selected?.entity_key === score.entity_key ? 'active' : ''}
        onClick={() => onSelect(score)}
        onContextMenu={event => onContextMenu?.(event, score)}
        title={score.summary}
      >
        <span className="parallel-review-name">{index + 1}. {score.name}<MarketBoardBadge instrument={score}/></span>
        <span className={`score-grade grade-${score.grade.toLowerCase()}`}>{score.total_score.toFixed(1)}</span>
        <small>{score.eligible ? '符合条件' : '观察'} · {score.summary}</small>
      </button>)}
      {scores.length > limit && <button onClick={() => setLimit(value => value + 10)}>显示更多</button>}
    </div>
  </section>
}
