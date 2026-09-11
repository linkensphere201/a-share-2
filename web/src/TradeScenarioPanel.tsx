import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import {
  isVolumeZoneTarget,
  readPrimaryStructuralScenario,
  setupFamilyLabel,
  targetBasisLabel,
  type StructuralTradeScenario,
} from './tradeScenarioProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export function TradeScenarioPanel({
  run,
  selectedTargetLabel,
  visible = true,
  onTargetChange,
  onVisibleChange,
  onHighlightItemChange,
}: {
  run: TrendAnalysisRun
  selectedTargetLabel?: string
  visible?: boolean
  onTargetChange?: (label: string) => void
  onVisibleChange?: (visible: boolean) => void
  onHighlightItemChange: (itemId?: string) => void
}) {
  const [targetFilter, setTargetFilter] = useState<'all' | 'volume-zone'>('all')
  const scenario = readPrimaryStructuralScenario(run)
  if (!scenario) return null
  const activeTarget = scenario.targets.find(item => item.label === selectedTargetLabel)
    ?? scenario.targets.find(item => item.label === scenario.selectedTargetLabel)
    ?? scenario.targets[0]
  const setupEvidence = scenario.evidenceItemIds[0]
  const invalidationEvidence = scenario.invalidationEvidenceItemIds[0]
  const volumeTargets = scenario.targets.filter(isVolumeZoneTarget)
  const visibleTargets = targetFilter === 'volume-zone' ? volumeTargets : scenario.targets
  return <section className={`trade-scenario-panel ${scenario.state}`} aria-label="盈亏比场景">
    <h3>
      <span>交易场景</span>
      <button
        title={visible ? '隐藏盈亏比图层' : '显示盈亏比图层'}
        aria-label={visible ? '隐藏盈亏比图层' : '显示盈亏比图层'}
        aria-pressed={visible}
        onClick={() => onVisibleChange?.(!visible)}
      >{visible ? <Eye size={12}/> : <EyeOff size={12}/>}</button>
    </h3>
    <div className="trade-scenario-summary">
      <button
        className="trade-scenario-setup"
        onPointerEnter={() => onHighlightItemChange(setupEvidence)}
        onPointerLeave={() => onHighlightItemChange(undefined)}
      >
        <span>{directionLabel(scenario.direction)} · {horizonLabel(scenario.horizon)}</span>
        <small>{stateLabel(scenario.state)} · {setupFamilyLabel(scenario.setupFamily)}</small>
      </button>
      <dl>
        <dt>入场</dt><dd>{scenario.entryPrice.toFixed(2)}</dd>
        <dt>失效</dt><dd
          onPointerEnter={() => onHighlightItemChange(invalidationEvidence)}
          onPointerLeave={() => onHighlightItemChange(undefined)}
        >{scenario.invalidationPrice.toFixed(2)} <small>-{scenario.riskPercent.toFixed(2)}%</small></dd>
      </dl>
      {scenario.targets.length > 0 ? <>
        <div className="trade-scenario-target-filter" role="group" aria-label="目标来源筛选">
          <button className={targetFilter === 'all' ? 'active' : ''} aria-pressed={targetFilter === 'all'} onClick={() => setTargetFilter('all')}>全部目标</button>
          <button
            className={targetFilter === 'volume-zone' ? 'active' : ''}
            aria-pressed={targetFilter === 'volume-zone'}
            disabled={volumeTargets.length === 0}
            onClick={() => {
              setTargetFilter('volume-zone')
              if (volumeTargets.length > 0 && !activeTarget?.basis.includes('estimated-volume-at-price')) {
                onTargetChange?.(volumeTargets[0].label)
              }
            }}
          >成交密集区</button>
        </div>
        <div className="trade-scenario-targets" role="group" aria-label="盈亏比目标位">
          {visibleTargets.map(target => <button
            key={target.label}
            className={activeTarget?.label === target.label ? 'active' : ''}
            aria-pressed={activeTarget?.label === target.label}
            onClick={() => onTargetChange?.(target.label)}
            onPointerEnter={() => onHighlightItemChange(target.evidenceItemIds[0])}
            onPointerLeave={() => onHighlightItemChange(undefined)}
          >
            <span>{target.label}<small>{targetBasisLabel(target.basis)}</small></span>
            <span className="trade-scenario-target-value">{target.price.toFixed(2)}<small>盈亏比 {target.stressedRiskRewardRatio?.toFixed(2) ?? '-'}</small></span>
          </button>)}
        </div>
        {activeTarget && <div className="trade-scenario-ratios">
          <Ratio label="原始盈亏比" value={activeTarget.riskRewardRatio}/>
          <Ratio label="压力盈亏比" value={activeTarget.stressedRiskRewardRatio}/>
          <small>{targetBasisLabel(activeTarget.basis)}</small>
        </div>}
      </> : <p className="trade-scenario-unavailable">当前结构没有可复现的目标位，不给出盈亏比。</p>}
    </div>
  </section>
}

function Ratio({ label, value }: { label: string; value?: number }) {
  return <span>{label}<strong>{value === undefined ? '-' : value.toFixed(2)}</strong></span>
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
