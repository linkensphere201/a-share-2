import { useState } from 'react'
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Focus,
  Info,
  RefreshCw,
  RotateCcw,
  Settings2,
  TrendingUp,
  X,
} from 'lucide-react'
import {
  createTradingSystemWindowState,
  normalizeTrendTradingSystemSettings,
  tradingSystemRegistry,
  type TradingSystemWindowState,
  type TrendTradingSystemSettings,
} from './tradingSystems'
import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

type TradingSystemControlsProps = {
  instrumentKind: string
  state: TradingSystemWindowState
  breakoutState?: GeneratedBreakoutState
  analysisRun?: TrendAnalysisRun | null
  onChange: (state: TradingSystemWindowState) => void
  recalculationState?: 'idle' | 'running' | 'failed'
  onRecalculate: (state: TradingSystemWindowState, refreshData: boolean) => void | Promise<void>
  explanationOpen?: boolean
  onExplanationOpenChange?: (open: boolean) => void
  aiAnalysisAvailable?: boolean
  aiAnalysisOpen?: boolean
  onAiAnalysisOpenChange?: (open: boolean) => void
  recalculationAvailable?: boolean
  recalculationDisabledReason?: string
  embedded?: boolean
}

export function TradingSystemControls({
  instrumentKind,
  state,
  breakoutState,
  analysisRun,
  recalculationState = 'idle',
  onChange,
  onRecalculate,
  explanationOpen = false,
  onExplanationOpenChange,
  aiAnalysisAvailable = false,
  aiAnalysisOpen = false,
  onAiAnalysisOpenChange,
  recalculationAvailable = true,
  recalculationDisabledReason,
  embedded = false,
}: TradingSystemControlsProps) {
  const descriptor = tradingSystemRegistry.get('trend')!
  const supported = descriptor.supportedInstrumentKinds.includes(instrumentKind)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [draftSettings, setDraftSettings] = useState<TrendTradingSystemSettings>(() => (
    normalizeTrendTradingSystemSettings(state.settings)
  ))
  const [draftLayers, setDraftLayers] = useState<Record<string, boolean>>(() => ({ ...state.layers }))
  const eventLabel = breakoutState ? trendEventLabel(breakoutState) : undefined

  const openSettings = () => {
    setDraftSettings(normalizeTrendTradingSystemSettings(state.settings))
    setDraftLayers({ ...state.layers })
    setSettingsOpen(true)
  }

  const save = (recalculate: boolean) => {
    const settings = normalizeTrendTradingSystemSettings(draftSettings)
    const settingsChanged = JSON.stringify(settings) !== JSON.stringify(
      normalizeTrendTradingSystemSettings(state.settings),
    )
    const next: TradingSystemWindowState = {
      ...state,
      layers: Object.fromEntries(descriptor.layers.map(layer => [
        layer.id,
        typeof draftLayers[layer.id] === 'boolean' ? draftLayers[layer.id] : layer.defaultVisible,
      ])),
      settings,
      settingsRevision: settingsChanged ? state.settingsRevision + 1 : state.settingsRevision,
      analysisStatus: settingsChanged && state.analysisStatus === 'current' ? 'stale' : state.analysisStatus,
    }
    onChange(next)
    setSettingsOpen(false)
    if (recalculate) void onRecalculate(next, false)
  }

  const restoreDefaults = () => {
    const defaults = createTradingSystemWindowState(descriptor)
    setDraftSettings(normalizeTrendTradingSystemSettings(defaults.settings))
    setDraftLayers({ ...defaults.layers })
  }

  return (
    <div className={embedded ? 'trading-system-controls embedded' : state.expanded ? 'trading-system-controls' : 'trading-system-controls collapsed'} onPointerDown={event => event.stopPropagation()}>
      <div className="trading-system-control-actions">
        <button
          className={state.enabled ? 'active' : ''}
          title={state.enabled ? '停用趋势交易体系' : '启用趋势交易体系'}
          aria-label={state.enabled ? '停用趋势交易体系' : '启用趋势交易体系'}
          aria-pressed={state.enabled}
          disabled={!supported}
          onClick={() => onChange({ ...state, enabled: !state.enabled })}
        >{state.enabled ? <Eye size={13}/> : <EyeOff size={13}/>}</button>
        <button
          className={state.isolate ? 'active' : ''}
          title={state.isolate ? '退出趋势隔离' : '仅查看趋势体系'}
          aria-label={state.isolate ? '退出趋势隔离' : '仅查看趋势体系'}
          aria-pressed={state.isolate}
          disabled={!state.enabled || !supported}
          onClick={() => onChange({ ...state, isolate: !state.isolate })}
        ><Focus size={13}/></button>
        <button
          title={!recalculationAvailable
            ? recalculationDisabledReason ?? '当前分析结果不可重新测算'
            : eventLabel ? `\u66f4\u65b0\u6d4b\u7b97 \u00b7 ${breakoutState?.preview ? '\u76d8\u4e2d\u9884\u89c8 \u00b7 ' : ''}${eventLabel}` : '\u66f4\u65b0\u6d4b\u7b97'}
          aria-label="更新测算"
          className={breakoutState ? `trend-recalculate-event ${breakoutState.state}` : ''}
          data-event-label={eventLabel}
          disabled={!state.enabled || !supported || !recalculationAvailable || recalculationState === 'running'}
          onClick={() => void onRecalculate(state, true)}
        >
          <RefreshCw size={13}/>
          {breakoutState && <span
            className="trend-recalculate-event-dot"
            data-testid="trend-recalculate-event"
            data-evidence-trigger="true"
            title={`${breakoutState.preview ? '盘中预览 · ' : ''}${eventLabel}`}
            onClick={event => {
              event.stopPropagation()
              onExplanationOpenChange?.(true)
            }}
          />}
        </button>
        <button
          title="趋势分析结果说明"
          aria-label="打开趋势分析结果说明"
          disabled={!analysisRun}
          className={explanationOpen ? 'active' : ''}
          aria-pressed={explanationOpen}
          onClick={() => onExplanationOpenChange?.(!explanationOpen)}
        ><Info size={13}/></button>
        <button
          title="AI形态分析"
          aria-label="打开AI形态分析"
          disabled={!aiAnalysisAvailable}
          className={aiAnalysisOpen ? 'active' : ''}
          aria-pressed={aiAnalysisOpen}
          onClick={() => onAiAnalysisOpenChange?.(!aiAnalysisOpen)}
        ><Bot size={13}/></button>
        <button
          className={settingsOpen ? 'active' : ''}
          title="趋势交易体系设置"
          aria-label="趋势交易体系设置"
          aria-expanded={settingsOpen}
          disabled={!supported}
          onClick={openSettings}
        ><Settings2 size={13}/></button>
        {recalculationState === 'running'
          ? <span className="trading-system-status running">正在测算</span>
          : recalculationState === 'failed'
            ? <span className="trading-system-status failed">测算失败</span>
            : state.analysisStatus !== 'current' && (
              <span className={`trading-system-status ${state.analysisStatus}`}>
                {state.analysisStatus === 'stale' ? '已过期' : '待测算'}
              </span>
            )}
      </div>
      {!embedded && <button
        className="trading-system-collapse"
        title={state.expanded ? '收起趋势交易体系' : '展开趋势交易体系'}
        aria-label={state.expanded ? '收起趋势交易体系' : '展开趋势交易体系'}
        aria-expanded={state.expanded}
        onClick={() => onChange({ ...state, expanded: !state.expanded })}
      >
        <TrendingUp size={13}/>
        {state.expanded ? <ChevronLeft size={10}/> : <ChevronRight size={10}/>} 
      </button>}

      {settingsOpen && (
        <div className="trading-system-settings" role="dialog" aria-label="趋势交易体系设置面板">
          <header>
            <span><TrendingUp size={14}/>趋势交易体系</span>
            <button title="关闭设置" aria-label="关闭趋势交易体系设置" onClick={() => setSettingsOpen(false)}><X size={13}/></button>
          </header>
          <div className="trading-system-settings-body">
            <fieldset className="trading-system-horizons">
              <legend>分析周期</legend>
              <label>短期<input aria-label="短期交易日" type="number" min="20" max="120" value={draftSettings.shortHorizonBars} onChange={event => setDraftSettings(current => ({ ...current, shortHorizonBars: Number(event.target.value) }))}/></label>
              <label>中期<input aria-label="中期交易日" type="number" min="60" max="500" value={draftSettings.mediumHorizonBars} onChange={event => setDraftSettings(current => ({ ...current, mediumHorizonBars: Number(event.target.value) }))}/></label>
              <label>长期<input aria-label="长期交易日" type="number" min="120" max="1250" value={draftSettings.longHorizonBars} onChange={event => setDraftSettings(current => ({ ...current, longHorizonBars: Number(event.target.value) }))}/></label>
            </fieldset>
            <fieldset>
              <legend>K线级别</legend>
              <label><input aria-label="分析日线" type="checkbox" checked={draftSettings.dailyEnabled} onChange={() => setDraftSettings(current => toggleTimeframe(current, 'dailyEnabled'))}/>日线</label>
              <label><input aria-label="分析周线" type="checkbox" checked={draftSettings.weeklyEnabled} onChange={() => setDraftSettings(current => toggleTimeframe(current, 'weeklyEnabled'))}/>周线</label>
              <label><input aria-label="分析月线" type="checkbox" checked={draftSettings.monthlyEnabled} onChange={() => setDraftSettings(current => toggleTimeframe(current, 'monthlyEnabled'))}/>月线</label>
            </fieldset>
            <fieldset>
              <legend>分析选项</legend>
              <label><input aria-label="盘中临时K线参与预览" type="checkbox" checked={draftSettings.provisionalPreview} onChange={event => setDraftSettings(current => ({ ...current, provisionalPreview: event.target.checked }))}/>盘中预览</label>
              <label><input aria-label="显示待确认拐点" type="checkbox" checked={draftSettings.showTentativePivots} onChange={event => setDraftSettings(current => ({ ...current, showTentativePivots: event.target.checked }))}/>待确认拐点</label>
            </fieldset>
            <fieldset className="trading-system-layers">
              <legend>分析图层</legend>
              {descriptor.layers.map(layer => (
                <label key={layer.id}><input aria-label={`显示${layer.label}`} type="checkbox" checked={draftLayers[layer.id] !== false} onChange={event => setDraftLayers(current => ({ ...current, [layer.id]: event.target.checked }))}/>{layer.label}</label>
              ))}
            </fieldset>
          </div>
          <footer>
            <button className="icon-command" title="恢复默认" aria-label="恢复趋势体系默认设置" onClick={restoreDefaults}><RotateCcw size={12}/></button>
            <span/>
            <button onClick={() => setSettingsOpen(false)}>取消</button>
            <button onClick={() => save(false)}>保存</button>
            <button className="primary" disabled={!recalculationAvailable || recalculationState === 'running'} onClick={() => save(true)}>保存并重新测算</button>
          </footer>
        </div>
      )}
    </div>
  )
}

function trendEventLabel(value: GeneratedBreakoutState): string {
  if (value.eventKind === 'upward-breakout') return '向上突破'
  if (value.eventKind === 'downward-breakdown') return '向下破位'
  if (value.eventKind === 'retest') return '回踩确认'
  if (value.eventKind === 'false-breakout-risk') return '假突破风险'
  if (value.eventKind === 'no-structural-change') return '无结构变化'
  return ({
    forming: '形成中', ready: '准备', triggered: '已触发', confirmed: '已确认',
    retesting: '回踩中', continuing: '延续', failed: '失败',
    invalidated: '失效', stale: '已过期',
  })[value.state]
}

function toggleTimeframe(
  settings: TrendTradingSystemSettings,
  key: 'dailyEnabled' | 'weeklyEnabled' | 'monthlyEnabled',
): TrendTradingSystemSettings {
  const enabledCount = Number(settings.dailyEnabled) + Number(settings.weeklyEnabled) + Number(settings.monthlyEnabled)
  if (settings[key] && enabledCount === 1) return settings
  return { ...settings, [key]: !settings[key] }
}
