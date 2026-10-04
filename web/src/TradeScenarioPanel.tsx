import { AnalysisOverlayToggle } from './AnalysisOverlayToggle'
import {
  readPrimaryStructuralScenario,
  setupFamilyLabel,
  targetBasisLabel,
  trendSpaceLabel,
  type StructuralTradeScenario,
} from './tradeScenarioProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export function TradeScenarioPanel({
  run,
  visible = false,
  showVisibilityControl = true,
  onVisibleChange,
  onHighlightItemChange,
}: {
  run: TrendAnalysisRun
  selectedTargetLabel?: string
  visible?: boolean
  showVisibilityControl?: boolean
  onTargetChange?: (label: string) => void
  onVisibleChange?: (visible: boolean) => void
  onHighlightItemChange: (itemId?: string) => void
}) {
  const scenario = readPrimaryStructuralScenario(run)
  if (!scenario) return null
  const activeTarget = scenario.targets[0]
  const setupEvidence = scenario.evidenceItemIds[0]
  const invalidationEvidence = scenario.invalidationEvidenceItemIds[0]
  return <section className={`trade-scenario-panel ${scenario.state}`} aria-label="盈亏比场景">
    <h3>
      <span>{scenario.direction === 'long' ? '多头参考空间' : '下行风险情景'}</span>
      {showVisibilityControl && <AnalysisOverlayToggle label="显示趋势目标与盈亏比"
        checked={visible} onChange={onVisibleChange}>目标区域与盈亏比</AnalysisOverlayToggle>}
    </h3>
    <div className="trade-scenario-summary">
      {scenario.legacy && <p role="status">旧版分析，需重新测算；以下仅为历史价格参考，不沿用旧资格结论。</p>}
      {scenario.qualificationBlocked && <p role="status">复权依据不完整，仅保留价格参考，不判定盈亏比达标。</p>}
      <button
        className="trade-scenario-setup"
        onPointerEnter={() => onHighlightItemChange(setupEvidence)}
        onPointerLeave={() => onHighlightItemChange(undefined)}
      >
        <span>{directionLabel(scenario.direction)} · {horizonLabel(scenario.horizon)}</span>
        <small>{stateLabel(scenario.state)} · {setupFamilyLabel(scenario.setupFamily)}</small>
      </button>
      <dl>
        <dt>{scenario.state === 'waiting-trigger' ? '计划触发' : '参考价格'}</dt><dd>{scenario.entryPrice.toFixed(2)}</dd>
        <dt>结构止损</dt><dd
          onPointerEnter={() => onHighlightItemChange(invalidationEvidence)}
          onPointerLeave={() => onHighlightItemChange(undefined)}
        >{scenario.invalidationPrice.toFixed(2)} <small>-{scenario.riskPercent.toFixed(2)}%</small></dd>
      </dl>
      {activeTarget ? <>
        <dl onPointerEnter={() => onHighlightItemChange(activeTarget.evidenceItemIds[0])}
          onPointerLeave={() => onHighlightItemChange(undefined)}>
          <dt>最近目标区</dt><dd>{activeTarget.zone
            ? `${activeTarget.zone.lower.toFixed(2)} ~ ${activeTarget.zone.upper.toFixed(2)}`
            : activeTarget.price.toFixed(2)}</dd>
          <dt>采用目标价</dt><dd>{activeTarget.price.toFixed(2)}</dd>
          <dt>潜在收益</dt><dd>{scenario.rewardDistance?.toFixed(4) ?? '-'}</dd>
          <dt>潜在风险</dt><dd>{scenario.riskDistance?.toFixed(4) ?? '-'}</dd>
        </dl>
        <p className="trade-scenario-verdict" role="status">{trendSpaceLabel(scenario.spaceStatus)}</p>
        <div className="trade-scenario-ratios">
          <Ratio label="原始盈亏比" value={activeTarget.riskRewardRatio}/>
          <Ratio label="压力盈亏比" value={activeTarget.stressedRiskRewardRatio}/>
          <small>{targetBasisLabel(activeTarget.basis)}</small>
        </div>
      </> : <p className="trade-scenario-unavailable">当前结构没有可复现的目标位，不给出盈亏比。</p>}
    </div>
  </section>
}

function Ratio({ label, value }: { label: string; value?: number }) {
  return <span>{label}<strong title={value === undefined ? undefined : String(value)}>{value === undefined ? '-' : `${value.toFixed(2)} 倍`}</strong></span>
}

function directionLabel(value: StructuralTradeScenario['direction']): string {
  return value === 'long' ? '向上场景' : '向下场景'
}

function horizonLabel(value: string): string {
  return value === 'short' ? '短期' : value === 'medium' ? '中期' : value === 'long' ? '长期' : value
}

function stateLabel(value: StructuralTradeScenario['state']): string {
  return ({
    'waiting-trigger': '等待触发',
    triggered: '已经触发',
    retest: '回踩确认',
    invalidated: '已经失效',
    extended: '偏离入场位',
    'no-entry': '暂无入场场景',
  })[value]
}
