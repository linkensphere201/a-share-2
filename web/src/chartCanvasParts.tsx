import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { AlertTriangle, Eye, EyeOff, Trash2, X } from 'lucide-react'
import type { DrawingMigrationCandidate, TrendLineDash, TrendLineDrawing } from './drawingStore'
import { barsInRenderPeriod, type LineGeometry } from './trendLines'
import type { ThemeDefinition } from './themeStore'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import type { TrendReviewGeometryTarget } from './trendReviewGeometry'
import type {
  GeneratedBreakoutState,
  GeneratedPatternGeometry,
  GeneratedPivotGeometry,
  GeneratedTrendLineGeometry,
  GeneratedZoneGeometry,
  ProjectedReviewGeometryHandle,
} from './generatedAnalysisProjection'
import type {
  MarketAnnotationGeometry,
  MeasurementGeometry,
  ProjectedTrendLine,
} from './chartProjection'
import {
  candleColor,
  calculateChangePercent,
  clamp,
  type DailyBar,
  type MacdPoint,
  type RangeMeasurement,
  type Readout,
  type RenderBar,
} from './chartData'
import type { ChartPaneRatios } from './workspace'
import type {
  HistogramData,
  IChartApi,
  IPaneApi,
  IRange,
  ISeriesApi,
  LineData,
  Time,
} from 'lightweight-charts'

export type SelectionBox = { left: number; top: number; width: number; height: number }
export type RangeSelection = {
  first: RenderBar
  last: RenderBar
  count: number
  box: SelectionBox
  menuLeft: number
  menuTop: number
}
export type ViewportSnapshot = {
  logical: IRange<number>
  dataCount: number
}

const rising = '#ef5350'
const falling = '#26a269'
const trendLineColors = [
  '#f0b85a', '#ef5350', '#ff7a45', '#e85d91',
  '#26a269', '#8ac926', '#26b5a8', '#49c6e5',
  '#57a7d9', '#4776e6', '#7b68ee', '#b984cc',
  '#d8dde6', '#9aa6b4', '#f4d35e', '#00c2a8',
] as const
const trendLineDashOptions: Array<{ value: TrendLineDash; label: string }> = [
  { value: 'solid', label: '实线' },
  { value: 'dotted', label: '点线' },
  { value: 'dashed', label: '短虚线' },
  { value: 'long-dashed', label: '长虚线' },
  { value: 'dash-dot', label: '点划线' },
]
export function PaneHeader({ kind, top, onHide }: { kind: 'volume' | 'macd' | 'open-interest'; top: number; onHide: () => void }) {
  const label = kind === 'volume' ? '成交量' : kind === 'macd' ? 'MACD' : '持仓量'
  return (
    <div className={`chart-pane-header chart-pane-header-${kind}`} style={{ top: top + 2 }} onPointerDown={event => event.stopPropagation()}>
      {kind === 'volume'
        ? <span className="chart-pane-title">VOL</span>
        : kind === 'open-interest'
          ? <span className="chart-pane-title">OI</span>
          : <div className="macd-legend" aria-label="MACD 图例">
            <span>MACD</span><i className="macd-dif"/>DIF<i className="macd-dea"/>DEA<i className="macd-bars"/>柱
          </div>}
      <button title={`隐藏${label}栏`} aria-label={`隐藏${label}栏`} onClick={onHide}><EyeOff size={11}/></button>
    </div>
  )
}

export function TrendLineManager({
  drawings,
  migrationCandidates,
  selectedId,
  onSelect,
  onDashChange,
  onColorChange,
  onVisibilityChange,
  onResolveMigration,
  onClose,
}: {
  drawings: TrendLineDrawing[]
  migrationCandidates: DrawingMigrationCandidate[]
  selectedId?: string
  onSelect: (id: string) => void
  onDashChange: (id: string, dash: TrendLineDash) => void
  onColorChange: (id: string, color: string) => void
  onVisibilityChange: (id: string) => void
  onResolveMigration: (candidateId: string, action: 'migrate' | 'reject') => void
  onClose: () => void
}) {
  const selected = drawings.find(drawing => drawing.id === selectedId)
  return (
    <div className="trend-line-manager" onPointerDown={event => event.stopPropagation()}>
      <header><strong>趋势线</strong><span>{drawings.length}</span><button title="关闭趋势线管理" aria-label="关闭趋势线管理" onClick={onClose}><X size={12}/></button></header>
      {migrationCandidates.length > 0 && <div className="trend-line-migrations">
        {migrationCandidates.map(candidate => <div key={candidate.id}>
          <span>{migrationCandidateLabel(candidate)}<small>{candidate.drawingCount} 条趋势线</small></span>
          {candidate.canMigrate && <button onClick={() => onResolveMigration(candidate.id, 'migrate')}>迁移</button>}
          <button onClick={() => onResolveMigration(candidate.id, 'reject')}>保持隔离</button>
        </div>)}
      </div>}
      <div className="trend-line-list">
        {drawings.length === 0 && <span className="trend-line-empty">暂无趋势线</span>}
        {drawings.map((drawing, index) => (
          <div key={drawing.id} className={drawing.id === selectedId ? 'trend-line-item selected' : 'trend-line-item'}>
            <button className="trend-line-select" onClick={() => onSelect(drawing.id)}>
              <i style={{ background: drawing.style.color }}/>
              <span>趋势线 {index + 1}<small>{drawing.anchors[0].date} - {drawing.anchors[1].date}</small></span>
            </button>
            <button
              className="trend-line-visibility"
              title={drawing.visible ? '隐藏趋势线' : '显示趋势线'}
              aria-label={`${drawing.visible ? '隐藏' : '显示'}趋势线 ${index + 1}`}
              onClick={() => onVisibilityChange(drawing.id)}
            >{drawing.visible ? <Eye size={13}/> : <EyeOff size={13}/>}</button>
          </div>
        ))}
      </div>
      {selected && (
        <div className="trend-line-settings">
          <label>线型
            <select aria-label="趋势线线型" value={selected.style.dash} onChange={event => onDashChange(selected.id, event.target.value as TrendLineDash)}>
              {trendLineDashOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <div className="trend-line-colors" aria-label="趋势线颜色">
            {trendLineColors.map(color => (
              <button
                key={color}
                className={selected.style.color === color ? 'active' : ''}
                title={`使用颜色 ${color}`}
                aria-label={`趋势线颜色 ${color}`}
                style={{ '--trend-color': color } as CSSProperties}
                onClick={() => onColorChange(selected.id, color)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function migrationCandidateLabel(candidate: DrawingMigrationCandidate): string {
  if (candidate.kind === 'legacy-unknown') return '旧版期货线（基准未知）'
  if (candidate.kind === 'different-price-basis') {
    return `不兼容价格基准：${candidate.sourcePriceBasis ?? '未知'}`
  }
  return `旧规则：${candidate.sourceRuleVersion ?? '未知'}`
}

export function TrendLineOverlay({
  lines,
  draft,
  selectedId,
  movingId,
  editingAnchor,
  onSelect,
  onMoveStart,
  onMove,
  onMoveEnd,
  onMoveCancel,
  onAnchorMoveStart,
  onAnchorMove,
  onAnchorMoveEnd,
  onAnchorMoveCancel,
}: {
  lines: ProjectedTrendLine[]
  draft?: LineGeometry
  selectedId?: string
  movingId?: string
  editingAnchor?: { drawingId: string; anchorIndex: 0 | 1 }
  onSelect: (id: string) => void
  onMoveStart: (event: ReactPointerEvent<SVGLineElement>, drawing: TrendLineDrawing) => void
  onMove: (event: ReactPointerEvent<SVGLineElement>) => void
  onMoveEnd: (event: ReactPointerEvent<SVGLineElement>) => void
  onMoveCancel: (event: ReactPointerEvent<SVGLineElement>) => void
  onAnchorMoveStart: (event: ReactPointerEvent<SVGCircleElement>, drawing: TrendLineDrawing, anchorIndex: 0 | 1) => void
  onAnchorMove: (event: ReactPointerEvent<SVGCircleElement>) => void
  onAnchorMoveEnd: (event: ReactPointerEvent<SVGCircleElement>) => void
  onAnchorMoveCancel: (event: ReactPointerEvent<SVGCircleElement>) => void
}) {
  return (
    <div className="chart-trend-lines">
      <svg width="100%" height="100%" aria-label="趋势线图层">
        {lines.map(line => (
          <g key={line.drawing.id} className={[
            'trend-line',
            line.drawing.id === selectedId ? 'selected' : '',
            line.drawing.id === movingId ? 'moving' : '',
            line.drawing.id === editingAnchor?.drawingId ? 'editing-anchor' : '',
          ].filter(Boolean).join(' ')}>
            <line
              className="trend-line-hit"
              x1={line.line.x1}
              y1={line.line.y1}
              x2={line.line.x2}
              y2={line.line.y2}
              onPointerDown={event => {
                event.preventDefault()
                event.stopPropagation()
                onSelect(line.drawing.id)
                onMoveStart(event, line.drawing)
              }}
              onPointerMove={onMove}
              onPointerUp={onMoveEnd}
              onPointerCancel={onMoveCancel}
            />
            <line
              className="trend-line-stroke"
              x1={line.line.x1}
              y1={line.line.y1}
              x2={line.line.x2}
              y2={line.line.y2}
              stroke={line.drawing.style.color}
              strokeWidth={line.drawing.style.width}
              strokeDasharray={trendLineDashPattern(line.drawing.style.dash)}
              strokeLinecap={line.drawing.style.dash === 'dotted' ? 'round' : 'butt'}
            />
            {line.drawing.id === selectedId && ([0, 1] as const).map(anchorIndex => {
              const cx = anchorIndex === 0 ? line.anchors.x1 : line.anchors.x2
              const cy = anchorIndex === 0 ? line.anchors.y1 : line.anchors.y2
              return <g key={anchorIndex}>
                <circle
                  className="trend-line-anchor-hit"
                  data-anchor-index={anchorIndex}
                  cx={cx}
                  cy={cy}
                  r="10"
                  aria-label={`拖动趋势线${anchorIndex === 0 ? '起点' : '终点'}`}
                  onPointerDown={event => onAnchorMoveStart(event, line.drawing, anchorIndex)}
                  onPointerMove={onAnchorMove}
                  onPointerUp={onAnchorMoveEnd}
                  onPointerCancel={onAnchorMoveCancel}
                />
                <circle
                  className={[
                    'trend-line-anchor-handle',
                    editingAnchor?.drawingId === line.drawing.id && editingAnchor.anchorIndex === anchorIndex ? 'active' : '',
                  ].filter(Boolean).join(' ')}
                  cx={cx}
                  cy={cy}
                  r="4"
                />
              </g>
            })}
          </g>
        ))}
        {draft && (
          <g className="trend-line draft">
            <line className="trend-line-stroke" x1={draft.x1} y1={draft.y1} x2={draft.x2} y2={draft.y2}/>
            <circle cx={draft.x1} cy={draft.y1} r="3.5"/>
            <circle cx={draft.x2} cy={draft.y2} r="3.5"/>
          </g>
        )}
      </svg>
    </div>
  )
}

export function TrendReviewGeometryOverlay({
  handles,
  onMoveStart,
  onMove,
  onMoveEnd,
  onMoveCancel,
}: {
  handles: ProjectedReviewGeometryHandle[]
  onMoveStart: (event: ReactPointerEvent<SVGCircleElement>, handle: ProjectedReviewGeometryHandle) => void
  onMove: (event: ReactPointerEvent<SVGCircleElement>) => void
  onMoveEnd: (event: ReactPointerEvent<SVGCircleElement>) => void
  onMoveCancel: (event: ReactPointerEvent<SVGCircleElement>) => void
}) {
  return (
    <div className="trend-review-geometry-overlay" aria-label="人工复核几何编辑">
      <svg width="100%" height="100%">
        {handles.map(handle => (
          <g key={handle.id}>
            {handle.priceOnly && <line className="trend-review-price-guide" x1={handle.x} y1={handle.y} x2="100%" y2={handle.y}/>}
            <circle
              className="trend-review-geometry-hit"
              cx={handle.x}
              cy={handle.y}
              r={10}
              role="button"
              aria-label={`拖动${handle.label}`}
              onPointerDown={event => onMoveStart(event, handle)}
              onPointerMove={onMove}
              onPointerUp={onMoveEnd}
              onPointerCancel={onMoveCancel}
            />
            <circle className="trend-review-geometry-handle" cx={handle.x} cy={handle.y} r={4}/>
          </g>
        ))}
      </svg>
    </div>
  )
}

export function GeneratedAnalysisOverlay({
  pivots,
  lines,
  zones,
  patterns,
  breakoutState,
  run,
  preview,
  highlightedItemId,
}: {
  pivots: GeneratedPivotGeometry[]
  lines: GeneratedTrendLineGeometry[]
  zones: GeneratedZoneGeometry[]
  patterns: GeneratedPatternGeometry[]
  breakoutState?: GeneratedBreakoutState
  run: TrendAnalysisRun
  preview: boolean
  highlightedItemId?: string
}) {
  const labeled = new Set(pivots.slice(-4).map(item => item.id))
  return (
    <div className={highlightedItemId ? 'chart-generated-analysis has-highlight' : 'chart-generated-analysis'} aria-label="自动趋势分析图层">
      <svg width="100%" height="100%" aria-hidden="true">
        {zones.map(item => (
          <rect
            key={item.id}
            className={`generated-price-zone ${item.kind}${highlightedItemId === item.id ? ' highlighted' : ''}`}
            x={0}
            y={item.y}
            width={item.width}
            height={item.height}
          >
            <title>{item.kind === 'key-level'
              ? `关键位 ${formatPrice(item.lower)}-${formatPrice(item.upper)} · 评分 ${item.score.toFixed(2)}`
              : `日线估算成交密集区 ${formatPrice(item.lower)}-${formatPrice(item.upper)} · 占比 ${((item.estimatedShare ?? 0) * 100).toFixed(1)}%`}</title>
          </rect>
        ))}
        {patterns.map(item => (
          <g key={item.id} className={`generated-pattern ${item.state} ${item.primary ? 'primary' : 'alternative'}${highlightedItemId === item.id ? ' highlighted' : ''}`}>
            {item.boundaries.map((boundary, index) => (
              <line
                key={index}
                className="generated-pattern-boundary"
                x1={boundary.x1}
                y1={boundary.y1}
                x2={boundary.x2}
                y2={boundary.y2}
              />
            ))}
            {item.boundaries.length === 0 && <>
              <polyline points={item.points}/>
              <line
                className="generated-pattern-neckline"
                x1={item.neckline.x1}
                y1={item.neckline.y1}
                x2={item.neckline.x2}
                y2={item.neckline.y2}
              />
            </>}
            {item.showLabel && <text x={item.labelX} y={item.labelY}>{item.displayName}</text>}
            <title>{`${item.displayName} · ${item.state === 'forming' ? '形成中' : item.state === 'confirmed' ? '已确认' : '已失效'} · 评分 ${item.score.toFixed(2)}`}</title>
          </g>
        ))}
        {lines.map(item => (
          <g key={item.id} className={highlightedItemId === item.id ? 'generated-line-group highlighted' : 'generated-line-group'}>
            <line
              className={`generated-trend-line ${item.kind} ${item.horizon}${highlightedItemId === item.id ? ' highlighted' : ''}`}
              x1={item.line.x1}
              y1={item.line.y1}
              x2={item.line.x2}
              y2={item.line.y2}
            >
              <title>{`${item.label ? `${item.label} · ` : ''}${item.horizon === 'short' ? '短期' : '长期'}${item.kind === 'support' ? '支撑' : '压力'} · 评分 ${item.score.toFixed(2)} · 触碰 ${item.touchCount}`}</title>
            </line>
            {item.label && <text
              className="generated-line-code"
              x={Math.max(8, Math.min(item.line.x1, item.line.x2) + 8)}
              y={Math.max(14, Math.min(item.line.y1, item.line.y2) - 6)}
            >{item.label}</text>}
          </g>
        ))}
        {pivots.map(pivot => {
          const markerY = pivot.kind === 'high' ? pivot.y - 7 : pivot.y + 7
          const points = pivot.kind === 'high'
            ? `${pivot.x - 4},${markerY - 4} ${pivot.x + 4},${markerY - 4} ${pivot.x},${markerY + 3}`
            : `${pivot.x - 4},${markerY + 4} ${pivot.x + 4},${markerY + 4} ${pivot.x},${markerY - 3}`
          return <g key={pivot.id} className={`generated-pivot ${pivot.kind} ${pivot.tentative ? 'tentative' : 'confirmed'}`}>
            <line x1={pivot.x} y1={pivot.y} x2={pivot.x} y2={markerY}/>
            <polygon points={points}/>
            {labeled.has(pivot.id) && (
              <text
                x={pivot.x + 6}
                y={pivot.kind === 'high' ? markerY - 5 : markerY + 9}
              >{formatPrice(pivot.price)}</text>
            )}
            <title>{`${pivot.kind === 'high' ? '高点' : '低点'} ${pivot.pivotDate} · ${pivot.tentative ? '待确认' : `确认于 ${pivot.confirmedDate}`}`}</title>
          </g>
        })}
      </svg>
      <div className={`trend-analysis-evidence ${preview ? 'preview' : 'official'} ${run.stale ? 'stale' : ''}`}>
        <span>{preview ? '盘中预览' : '正式'}</span>
        <span>日线</span>
        <span>截至 {run.as_of_date}</span>
        <span>{pivots.length} 个枢轴</span>
        <span>{lines.length} 条趋势线</span>
        <span>{zones.length} 个价格区</span>
        <span>{patterns.length} 个形态</span>
        {breakoutState && (
          <span
            className={`breakout-state ${breakoutState.state}`}
            title={`边界 ${formatPrice(breakoutState.boundaryPrice)} · 失效位 ${formatPrice(breakoutState.invalidationPrice)} · 触发 ${breakoutState.triggerDate ?? '-'} · 确认 ${breakoutState.confirmationDate ?? '-'} · 失败 ${breakoutState.failureDate ?? '-'}`}
          >{breakoutState.preview ? '盘中预览 ' : ''}{breakoutStateLabel(breakoutState)}</span>
        )}
        {run.stale && <span>已过期</span>}
      </div>
    </div>
  )
}

function breakoutStateLabel(value: GeneratedBreakoutState): string {
  if (value.eventKind === 'upward-breakout') return '向上突破'
  if (value.eventKind === 'downward-breakdown') return '向下破位'
  if (value.eventKind === 'retest') return '回踩'
  if (value.eventKind === 'false-breakout-risk') return '假突破风险'
  if (value.eventKind === 'no-structural-change') return '无结构变化'
  return ({
    forming: '形成中', ready: '准备', triggered: '已触发', confirmed: '已确认',
    retesting: '回踩', continuing: '延续', failed: '失败',
    invalidated: '失效', stale: '陈旧',
  })[value.state]
}

function trendLineDashPattern(dash: TrendLineDash): string | undefined {
  if (dash === 'solid') return undefined
  if (dash === 'dotted') return '1 5'
  if (dash === 'long-dashed') return '14 7'
  if (dash === 'dash-dot') return '12 5 2 5'
  return '6 4'
}

export function MarketAnnotationOverlay({ geometry }: { geometry: MarketAnnotationGeometry }) {
  return (
    <div className="chart-market-annotations">
      <svg width={geometry.width} height={geometry.height} aria-hidden="true">
        {geometry.gaps.map(gap => {
          const top = Math.min(gap.y1, gap.y2)
          const height = Math.max(1, Math.abs(gap.y2 - gap.y1))
          const width = Math.max(1, gap.x2 - gap.x1)
          return (
            <g key={`${gap.direction}-${gap.startDate}`} className={`price-gap price-gap-${gap.direction}`}>
              <rect x={gap.x1} y={top} width={width} height={height}/>
            </g>
          )
        })}
        {geometry.extrema.map(point => {
          const drawLeft = point.x > geometry.width * 0.72
          const lineEnd = point.x + (drawLeft ? -24 : 24)
          const textX = lineEnd + (drawLeft ? -3 : 3)
          const textY = point.kind === 'high'
            ? clamp(point.y - 5, 12, geometry.height - 8)
            : clamp(point.y + 12, 12, geometry.height - 5)
          return (
            <g key={point.kind} className={`extreme-price extreme-price-${point.kind}`}>
              <circle cx={point.x} cy={point.y} r="2"/>
              <line x1={point.x} y1={point.y} x2={lineEnd} y2={point.y}/>
              <text x={textX} y={textY} textAnchor={drawLeft ? 'end' : 'start'}>{formatPrice(point.price)}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

export function MeasurementOverlay({
  measurement,
  geometry,
}: {
  measurement: RangeMeasurement
  geometry: MeasurementGeometry
}) {
  const tone = measurement.comparable
    ? measurement.changePercent >= 0 ? 'rise' : 'fall'
    : undefined
  const strokeTone = measurement.comparable
    ? measurement.changePercent >= 0 ? 'measurement-rise' : 'measurement-fall'
    : 'measurement-roll'
  const horizontalLabelX = (geometry.startX + geometry.endX) / 2
  const horizontalLabelY = clamp(geometry.startY - 7, 12, geometry.height - 6)
  return (
    <div className="chart-measurement-overlay">
      <svg width={geometry.width} height={geometry.height} aria-hidden="true">
        <polyline
          className={`measurement-triangle ${strokeTone}`}
          points={`${geometry.startX},${geometry.startY} ${geometry.endX},${geometry.startY} ${geometry.endX},${geometry.endY} ${geometry.startX},${geometry.startY}`}
        />
        <circle className={strokeTone} cx={geometry.startX} cy={geometry.startY} r="3"/>
        <circle className={strokeTone} cx={geometry.endX} cy={geometry.endY} r="3"/>
        <text className="measurement-duration" x={horizontalLabelX} y={horizontalLabelY} textAnchor="middle">
          {measurement.elapsedDays}天 · {measurement.kLineCount}根K线
        </text>
      </svg>
      <div className="chart-measurement-readout" style={{ left: geometry.labelLeft, top: geometry.labelTop }}>
        <span><small>{measurement.from}</small><b>开 {formatPrice(measurement.open)}</b></span>
        <span><small>{measurement.to}</small><b>收 {formatPrice(measurement.close)}</b></span>
        {measurement.comparable
          ? <strong className={tone}>涨跌 {formatChangePercent(measurement.changePercent)}</strong>
          : <strong className="roll-warning">跨 {measurement.rollEventCount} 次换月 · 区间涨跌不可比</strong>}
      </div>
    </div>
  )
}

export function ChartReadout({ value, instrumentName, futures }: {
  value: Readout
  instrumentName?: string
  futures: boolean
}) {
  const candleTone = value.changePercent === undefined
    ? value.close >= value.open ? 'rise' : 'fall'
    : value.changePercent >= 0 ? 'rise' : 'fall'
  const changeTone = value.changePercent === undefined
    ? undefined
    : value.changePercent >= 0 ? 'rise' : 'fall'
  return (
    <div className={futures ? 'chart-readout futures' : 'chart-readout'}>
      {futures && instrumentName && <span>{instrumentName}</span>}
      {value.bar_state === 'intraday'
        ? <span className={value.stale ? 'live-badge stale' : 'live-badge'}>{value.stale ? '盘中延迟' : '盘中'}</span>
        : futures && <span className="final-badge">正式</span>}
      {futures && value.roll_event && <span className="roll-badge">换月</span>}
      <span>{value.trade_date}</span>
      <span>开 <b>{formatPrice(value.open)}</b></span>
      <span>高 <b>{formatPrice(value.high)}</b></span>
      <span>低 <b>{formatPrice(value.low)}</b></span>
      <span>收 <b className={candleTone}>{formatPrice(value.close)}</b></span>
      <span>{futures ? '结算涨跌' : '涨跌'} <b className={changeTone}>{formatChangePercent(value.changePercent)}</b></span>
      {futures && value.previous_settlement != null && <span className="futures-detail">昨结 <b>{formatPrice(value.previous_settlement)}</b></span>}
      {futures && value.settlement != null && <span className="futures-detail">结算 <b>{formatPrice(value.settlement)}</b></span>}
      <span>量 <b>{formatVolume(value.volume)}</b></span>
      {futures && value.amount != null && <span className="futures-detail">额 <b>{formatVolume(value.amount)}</b></span>}
      {futures && value.open_interest != null && <span className="futures-detail">持仓 <b>{formatVolume(value.open_interest)}</b></span>}
      {futures && value.open_interest_change != null && <span className="futures-detail">增仓 <b className={value.open_interest_change >= 0 ? 'rise' : 'fall'}>{formatSignedVolume(value.open_interest_change)}</b></span>}
      {futures && value.mapped_contract_symbol && <span className="futures-detail">映射 <b>{value.mapped_contract_symbol}</b></span>}
      {value.ma5 !== undefined && <span className="ma5-value">MA5 {formatPrice(value.ma5)}</span>}
      {value.ma20 !== undefined && <span className="ma20-value">MA20 {formatPrice(value.ma20)}</span>}
      {value.ma60 !== undefined && <span className="ma60-value">MA60 {formatPrice(value.ma60)}</span>}
    </div>
  )
}

export function applyMacdSeries(
  values: MacdPoint[],
  times: Set<string>,
  dif: ISeriesApi<'Line'> | null,
  dea: ISeriesApi<'Line'> | null,
  histogram: ISeriesApi<'Histogram'> | null,
) {
  if (!dif || !dea || !histogram) return
  const visible = values.filter(item => times.has(item.time))
  dif.setData(visible.map(item => ({ time: item.time, value: item.dif })))
  dea.setData(visible.map(item => ({ time: item.time, value: item.dea })))
  histogram.setData(visible.map(item => ({
    time: item.time,
    value: item.histogram,
    color: item.histogram >= 0 ? `${rising}b3` : `${falling}b3`,
  })))
}

export function applyVolumeSeries(
  bars: RenderBar[],
  previousCloseByDate: Map<string, number>,
  volume: ISeriesApi<'Histogram'>,
) {
  volume.setData(bars.map(item => ({
    time: item.trade_date,
    value: item.volume,
    color: `${candleColor(item, previousCloseByDate.get(item.period_start))}99`,
  })))
}

export function applyOpenInterestSeries(
  bars: RenderBar[],
  series: ISeriesApi<'Histogram'>,
) {
  series.setData(bars.flatMap(item => item.open_interest == null
    ? []
    : [{ time: item.trade_date, value: item.open_interest, color: '#4f91b8aa' }]))
}

export function setPaneStretchFactors(chart: IChartApi) {
  chart.panes()[0]?.setStretchFactor(3)
  chart.panes().slice(1).forEach(pane => pane.setStretchFactor(1))
}

export function applyPaneRatios(
  chart: IChartApi,
  ratios: ChartPaneRatios,
  volume: IPaneApi<Time> | null,
  macd: IPaneApi<Time> | null,
  openInterest: IPaneApi<Time> | null,
) {
  chart.panes()[0]?.setStretchFactor(ratios.price)
  if (volume && ratios.volume) volume.setStretchFactor(ratios.volume)
  if (macd && ratios.macd) macd.setStretchFactor(ratios.macd)
  if (openInterest && ratios.openInterest) openInterest.setStretchFactor(ratios.openInterest)
}

export function emitPaneRatios(
  chart: IChartApi,
  volume: IPaneApi<Time> | null,
  macd: IPaneApi<Time> | null,
  openInterest: IPaneApi<Time> | null,
  callback?: (ratios: ChartPaneRatios) => void,
) {
  if (!callback) return
  const panes = chart.panes()
  const total = panes.reduce((sum, pane) => sum + pane.getHeight(), 0)
  if (total <= 0) return
  const ratio = (pane: IPaneApi<Time> | undefined | null) => pane
    ? Math.round((pane.getHeight() / total) * 1000) / 1000
    : undefined
  callback({
    price: ratio(panes[0]) ?? 1,
    volume: ratio(volume),
    macd: ratio(macd),
    openInterest: ratio(openInterest),
  })
}

export function captureViewport(chart: IChartApi | null, dataCount: number): ViewportSnapshot | undefined {
  const logical = chart?.timeScale().getVisibleLogicalRange()
  return logical ? { logical: { from: logical.from, to: logical.to }, dataCount } : undefined
}

export function resolveRangeSelection(
  chart: IChartApi,
  renderedBars: RenderBar[],
  box: SelectionBox,
  pointerX: number,
  pointerY: number,
  hostWidth: number,
  hostHeight: number,
): RangeSelection | undefined {
  if (renderedBars.length === 0) return undefined
  const leftLogical = chart.timeScale().coordinateToLogical(box.left)
  const rightLogical = chart.timeScale().coordinateToLogical(box.left + box.width)
  if (leftLogical === null || rightLogical === null) return undefined
  const leftIndex = clamp(Math.round(Number(leftLogical)), 0, renderedBars.length - 1)
  const rightIndex = clamp(Math.round(Number(rightLogical)), 0, renderedBars.length - 1)
  const firstIndex = Math.min(leftIndex, rightIndex)
  const lastIndex = Math.max(leftIndex, rightIndex)
  return {
    first: renderedBars[firstIndex],
    last: renderedBars[lastIndex],
    count: lastIndex - firstIndex + 1,
    box,
    menuLeft: clamp(pointerX + 6, 6, Math.max(6, hostWidth - 190)),
    menuTop: clamp(pointerY + 6, 6, Math.max(6, hostHeight - 78)),
  }
}

export function localPoint(event: ReactPointerEvent<HTMLDivElement>): { x: number; y: number } {
  const bounds = event.currentTarget.getBoundingClientRect()
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
}

export function chartPoint(event: ReactPointerEvent<Element>, host: HTMLDivElement | null): { x: number; y: number } {
  const bounds = host?.getBoundingClientRect()
  return bounds
    ? { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
    : { x: event.clientX, y: event.clientY }
}

export function rectangleFromPoints(startX: number, startY: number, endX: number, endY: number): SelectionBox {
  return {
    left: Math.min(startX, endX),
    top: Math.min(startY, endY),
    width: Math.abs(endX - startX),
    height: Math.abs(endY - startY),
  }
}

export function valueAt(series: ISeriesApi<'Line'>, param: { seriesData: Map<unknown, unknown> }): number | undefined {
  const item = param.seriesData.get(series) as LineData<Time> | undefined
  return item && 'value' in item ? item.value : undefined
}

function formatPrice(value: number): string {
  return value >= 1000 ? value.toFixed(1) : value.toFixed(2)
}

function formatChangePercent(value?: number): string {
  if (value === undefined) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatVolume(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return value.toLocaleString('zh-CN')
}

function formatSignedVolume(value: number): string {
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${formatVolume(Math.abs(value))}`
}
