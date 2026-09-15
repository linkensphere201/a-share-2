import { useCallback, useEffect, useState, type ReactNode } from 'react'

export const analysisLayers = {
  patternsVisible: '形态识别',
  volumeZonesVisible: '密集成交区',
  keyLevelsVisible: '关键价位与形态区间',
  shortTrendLinesVisible: '短期趋势线',
  mediumTrendLinesVisible: '中期趋势线',
  longTrendLinesVisible: '长期关键趋势线',
  pivotsVisible: '拐点标记',
  breakoutStateVisible: '突破状态',
} as const
type AnalysisLayer = keyof typeof analysisLayers
export type AnalysisLayerVisibility = Record<AnalysisLayer, boolean>
const hiddenLayers = Object.fromEntries(Object.keys(analysisLayers).map(key => [key, false])) as AnalysisLayerVisibility

export function useAnalysisLayers(contextKey: string) {
  const [selection, setSelection] = useState({ contextKey, layers: hiddenLayers })
  useEffect(() => {
    setSelection(current => current.contextKey === contextKey ? current : { contextKey, layers: hiddenLayers })
  }, [contextKey])
  const layers = selection.contextKey === contextKey ? selection.layers : hiddenLayers
  const onChange = (key: AnalysisLayer, visible: boolean) => {
    setSelection(current => ({ contextKey, layers: {
      ...(current.contextKey === contextKey ? current.layers : hiddenLayers), [key]: visible,
    } }))
  }
  return { layers, onChange }
}

export type AnalysisLayerControls = ReturnType<typeof useAnalysisLayers>

export function AnalysisLayerToggles({ layers, onChange }: AnalysisLayerControls) {
  return <>{(Object.keys(analysisLayers) as AnalysisLayer[]).map(key =>
    <AnalysisOverlayToggle key={key} label={`显示${analysisLayers[key]}`} checked={layers[key]}
      onChange={visible => onChange(key, visible)}>{analysisLayers[key]}</AnalysisOverlayToggle>)}</>
}

export function useAnalysisOverlayVisibility(contextKey: string) {
  const [selection, setSelection] = useState({ contextKey, visible: false })
  useEffect(() => {
    setSelection(current => current.contextKey === contextKey
      ? current : { contextKey, visible: false })
  }, [contextKey])
  const setVisible = useCallback((visible: boolean) => {
    setSelection({ contextKey, visible })
  }, [contextKey])
  // Gate the first render of a new context before its reset effect runs.
  return [selection.contextKey === contextKey && selection.visible, setVisible] as const
}

export function AnalysisOverlayToggle({ label, checked, onChange, children }: {
  label: string
  checked: boolean
  onChange?: (visible: boolean) => void
  children: ReactNode
}) {
  return <label className="analysis-overlay-toggle">
    <input type="checkbox" aria-label={label} checked={checked} disabled={!onChange}
      onChange={event => onChange?.(event.target.checked)}/>{children}
  </label>
}
